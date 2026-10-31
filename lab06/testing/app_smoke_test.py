"""Run nine application checks through the load balancer."""

import json
from pathlib import Path

import requests


def stack_urls():
    """Read deployment addresses from non-secret local outputs."""
    root = Path(__file__).resolve().parents[1]
    outputs = json.loads((root / "stack_outputs.json").read_text())

    # Import here so running this file directly also works.
    import sys
    sys.path.insert(0, str(root))
    from stack.config import Settings

    settings = Settings.from_file(root / ".env")
    base = f"http://{outputs['alb_dns_name']}"
    return (
        f"{base}:{settings.weather.listener_port}",
        f"{base}:{settings.advisor.listener_port}",
    )


def run_checks(weather, advisor):
    """Return all nine results, without stopping at the first failure."""
    results = []

    def get(url):
        try:
            response = requests.get(url, timeout=90)
            try:
                body = response.json()
            except ValueError:
                body = response.text
            return response.status_code, body
        except requests.RequestException:
            return 0, {}

    def check(name, passed, detail):
        results.append((name, bool(passed), detail))

    status, body = get(f"{weather}/weather?zip=60616")
    check(
        "Weather returns a forecast",
        status == 200 and isinstance(body, dict) and "forecast" in body,
        f"HTTP {status}",
    )

    status, body = get(f"{weather}/weather?zip=60616")
    check(
        "Repeat request is a cache hit",
        status == 200 and isinstance(body, dict)
        and body.get("cache") == "hit",
        f"HTTP {status}",
    )

    status, body = get(f"{weather}/history")
    check(
        "History contains the saved lookup",
        status == 200 and isinstance(body, dict)
        and any(row.get("zip") == "60616"
                for row in body.get("lookups", [])),
        f"HTTP {status}",
    )

    status, _ = get(f"{weather}/weather?zip=abc")
    check("Weather rejects invalid ZIP", status == 400, f"HTTP {status}")

    status, _ = get(f"{weather}/weather?zip=00000")
    check("Weather rejects unknown ZIP", status == 404, f"HTTP {status}")

    status, body = get(f"{advisor}/advice?zip=60616")
    check(
        "Advisor returns clothing advice",
        status == 200 and isinstance(body, dict)
        and bool(body.get("advice")),
        f"HTTP {status}",
    )
    source = body.get("source") if isinstance(body, dict) else None
    check(
        "Advice comes from Gemini",
        status == 200 and source == "gemini",
        f"source={source}",
    )

    status, _ = get(f"{advisor}/advice?zip=abc")
    check("Advisor rejects invalid ZIP", status == 400, f"HTTP {status}")

    status, body = get(advisor + "/")
    check(
        "Advisor home page contains a form",
        status == 200 and isinstance(body, str) and "<form" in body,
        f"HTTP {status}",
    )

    return results


def main():
    """Print each result and exit unsuccessfully if any check fails."""
    results = run_checks(*stack_urls())
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} | {name} | {detail}")

    passed = sum(result[1] for result in results)
    print(f"\n{passed}/{len(results)} smoke checks passed.")
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()