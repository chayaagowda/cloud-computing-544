"""Measure Weather recovery and Advisor behavior during an instance outage."""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests

from stack.clients import AwsClients
from stack.config import Settings
from stack.outputs import StackOutputs

pytestmark = pytest.mark.chaos


def test_weather_instance_recovery():
    """Terminate Weather, observe Advisor 502, and recover within the SLO."""
    root = Path(__file__).resolve().parents[1]
    settings = Settings.from_file(root / ".env")
    aws = AwsClients.create(settings.region)
    outputs = StackOutputs.load(root)

    weather = (
        f"http://{outputs.alb_dns_name}:"
        f"{settings.weather.listener_port}/weather?zip=60616"
    )
    advisor = (
        f"http://{outputs.alb_dns_name}:"
        f"{settings.advisor.listener_port}"
    )

    baseline = requests.get(weather, timeout=70)
    assert baseline.status_code == 200
    assert "forecast" in baseline.json()

    groups = aws.autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[settings.weather.asg_name]
    )["AutoScalingGroups"]
    assert len(groups) == 1
    group = groups[0]
    assert group["DesiredCapacity"] == 1
    assert len(group["Instances"]) == 1
    old_id = group["Instances"][0]["InstanceId"]

    def request_advisor():
        started = time.monotonic()
        response = requests.get(
            advisor + "/advice?zip=60616", timeout=75
        )
        return (
            response.status_code,
            response.json(),
            time.monotonic() - started,
        )

    print(f"\nTerminating Weather instance: {old_id}", flush=True)
    started = time.monotonic()
    aws.ec2.terminate_instances(InstanceIds=[old_id])

    limit = settings.recovery_slo_seconds
    outage_seen = False
    recovered_at = None
    advisor_future = None

    with ThreadPoolExecutor(max_workers=1) as executor:
        while time.monotonic() - started < limit:
            remaining = limit - (time.monotonic() - started)
            try:
                response = requests.get(
                    weather, timeout=min(8, max(0.1, remaining))
                )
                healthy = (
                    response.status_code == 200
                    and "forecast" in response.json()
                )
                status = str(response.status_code)
            except (requests.RequestException, ValueError):
                healthy = False
                status = "request_failed"

            elapsed = time.monotonic() - started
            print(
                f"{elapsed:6.1f}s | Weather: {status}",
                flush=True,
            )

            if not healthy and not outage_seen:
                outage_seen = True
                print("Weather outage observed.", flush=True)
                advisor_future = executor.submit(request_advisor)

                home = requests.get(advisor + "/", timeout=10)
                print(
                    f"Advisor home during outage: HTTP {home.status_code}",
                    flush=True,
                )
                assert home.status_code == 200

            if healthy and outage_seen:
                recovered_at = time.monotonic() - started
                break

            time.sleep(2)

        assert outage_seen, "No Weather outage was observed."
        assert recovered_at is not None, (
            f"Weather did not recover within {limit} seconds."
        )
        assert recovered_at <= limit

        status, body, duration = advisor_future.result(timeout=80)
        print(
            f"\nAdvisor during outage: HTTP {status}, "
            f"body={body}, seconds={duration:.2f}",
            flush=True,
        )
        assert status == 502
        assert body.get("error") == "weather_service_unavailable"

    current = aws.autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[settings.weather.asg_name]
    )["AutoScalingGroups"][0]
    replacements = [
        instance["InstanceId"]
        for instance in current["Instances"]
        if instance["InstanceId"] != old_id
    ]
    assert replacements, "No replacement instance found in the ASG."

    print(f"Replacement instance: {', '.join(replacements)}")
    print(f"Weather recovery: {recovered_at:.2f} seconds")
    print(f"Recovery SLO: {limit} seconds — PASS")