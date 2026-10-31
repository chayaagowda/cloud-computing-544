"""Probe an HTTP endpoint."""
import requests


def probe_once(url, timeout=3.0):
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False
