"""Small helpers shared by the integration tests."""
import time

import requests


def probe(url: str, timeout: float = 3.0) -> bool:
    """Return True if the URL responds with HTTP 200."""
    try:
        return requests.get(
            url, timeout=timeout
        ).status_code == 200
    except requests.RequestException:
        return False


def wait_until(check, timeout: int = 300, interval: int = 10):
    """Repeat a check until it succeeds or the timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = check()
        if result:
            return result
        time.sleep(interval)
    return None


def tier_url(outputs, settings, tier_name: str) -> str:
    port = settings.tier(tier_name).listener_port
    return f"http://{outputs.alb_dns_name}:{port}/"
