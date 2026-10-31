"""Terminate one tier's instance and measure recovery against an SLO."""
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
import shlex
import time

from aws_helpers import (
    get_inservice_instance_id,
    terminate_instance_via_asg,
)
from http_probe import probe_once


def load_env(path):
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if separator:
            parts = shlex.split(value, comments=True)
            values[key.strip()] = " ".join(parts)
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tier", choices=["uploader", "viewer"], required=True
    )
    parser.add_argument("--slo-seconds", type=float)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--max-wait", type=float, default=600.0)
    args = parser.parse_args()

    env = {
        **load_env(Path(__file__).resolve().parent.parent / ".env"),
        **load_env(Path(__file__).resolve().parent.parent / "stack_outputs.env"),
    }
    region = env.get("AWS_REGION", "us-east-2")
    prefix = args.tier.upper()
    asg_name = env[f"{prefix}_ASG_NAME"]
    port = env[f"{prefix}_LISTENER_PORT"]
    url = f"http://{env['ALB_DNS_NAME']}:{port}/"
    slo = (
        args.slo_seconds
        if args.slo_seconds is not None
        else float(env.get("RECOVERY_SLO_SECONDS", "180"))
    )

    if min(slo, args.poll_interval, args.max_wait) <= 0:
        parser.error("SLO, poll interval, and max wait must be positive.")

    print(f"Tier: {args.tier}", flush=True)
    print(f"URL: {url}", flush=True)
    print(f"SLO: {slo:.1f} seconds", flush=True)

    if not probe_once(url):
        raise RuntimeError(
            "Endpoint is unhealthy before testing. Fix it first."
        )

    instance_id = get_inservice_instance_id(asg_name, region)
    print(f"Original instance: {instance_id}", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log_path = Path(__file__).resolve().parent / (
        f"chaos_test_{args.tier}_{stamp}.csv"
    )

    print("Requesting termination through the ASG...", flush=True)
    start = time.monotonic()
    terminate_instance_via_asg(instance_id, region)

    down_at = None
    recovered_at = None
    previous_up = None
    consecutive_up = 0
    first_up_at = None

    with log_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["elapsed_seconds", "is_up"])

        while time.monotonic() - start < args.max_wait:
            is_up = probe_once(url)
            elapsed = time.monotonic() - start
            writer.writerow([round(elapsed, 2), is_up])
            handle.flush()

            if is_up != previous_up:
                state = "UP" if is_up else "DOWN"
                print(f"{elapsed:.1f}s: {state}", flush=True)
                previous_up = is_up

            if not is_up:
                if down_at is None:
                    down_at = elapsed
                consecutive_up = 0
                first_up_at = None
            elif down_at is not None:
                if consecutive_up == 0:
                    first_up_at = elapsed
                consecutive_up += 1
                if consecutive_up >= 3:
                    recovered_at = first_up_at
                    break

            time.sleep(args.poll_interval)

    print(f"CSV log: {log_path}", flush=True)

    if down_at is None:
        print(
            "INCONCLUSIVE: no outage was observed. "
            "Do not report this as a recovery PASS.",
            flush=True,
        )
        return 2

    if recovered_at is None:
        print(
            f"FAIL: recovery was not confirmed within "
            f"{args.max_wait:.0f}s.",
            flush=True,
        )
        return 1

    print(
        f"Recovery time: {recovered_at:.1f}s "
        "(from termination request)",
        flush=True,
    )
    print(
        f"Observed outage duration: {recovered_at - down_at:.1f}s",
        flush=True,
    )
    print("Recovery confirmed by three consecutive HTTP 200 probes.")
    if recovered_at <= slo:
        print(f"PASS: recovery met the {slo:.1f}s SLO.")
        return 0

    print(f"FAIL: recovery exceeded the {slo:.1f}s SLO.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
