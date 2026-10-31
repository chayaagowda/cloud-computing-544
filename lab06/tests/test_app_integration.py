"""Run smoke checks and verify cache behavior against the deployed stack."""

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from stack.config import Settings
from testing.app_smoke_test import run_checks, stack_urls

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def smoke_results():
    """Run the nine smoke checks once and share their results."""
    return run_checks(*stack_urls())


@pytest.mark.parametrize(
    "index",
    range(9),
    ids=[
        "weather_forecast",
        "cache_hit",
        "lookup_history",
        "weather_invalid_zip",
        "weather_unknown_zip",
        "advisor_advice",
        "gemini_source",
        "advisor_invalid_zip",
        "advisor_home_page",
    ],
)
def test_application_smoke(smoke_results, index):
    """Report each smoke-check result separately."""
    name, passed, detail = smoke_results[index]
    assert passed, f"{name}: {detail}"


def test_cache_miss_then_hit():
    """Wait for cache expiry, then verify a miss followed by a hit."""
    root = Path(__file__).resolve().parents[1]
    settings = Settings.from_file(root / ".env")
    weather, _ = stack_urls()

    history = requests.get(f"{weather}/history", timeout=15)
    assert history.status_code == 200

    rows = [
        row
        for row in history.json()["lookups"]
        if row["zip"] == "60616"
    ]

    if rows:
        latest = max(
            datetime.fromisoformat(
                row["created_at"].replace("Z", "+00:00")
            )
            for row in rows
        )
        age = (
            datetime.now(timezone.utc) - latest
        ).total_seconds()
        remaining = max(
            0, settings.cache_seconds - age + 5
        )

        while remaining > 0:
            print(
                f"Waiting for cache expiry: "
                f"{remaining:.0f} seconds remaining",
                flush=True,
            )
            pause = min(30, remaining)
            time.sleep(pause)
            remaining -= pause

    first = requests.get(
        f"{weather}/weather?zip=60616",
        timeout=70,
    )
    assert first.status_code == 200
    first_cache = first.json().get("cache")
    print("First lookup cache:", first_cache, flush=True)
    assert first_cache == "miss"

    second = requests.get(
        f"{weather}/weather?zip=60616",
        timeout=70,
    )
    assert second.status_code == 200
    second_cache = second.json().get("cache")
    print("Second lookup cache:", second_cache, flush=True)
    assert second_cache == "hit"