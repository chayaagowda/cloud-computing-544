"""Verify forecasts remain available during a database network outage."""

import json
import time
from pathlib import Path

import pytest
import requests

from stack.clients import AwsClients
from stack.config import Settings
from stack.database import database_ingress
from stack.outputs import StackOutputs
from stack.util import ignore_errors

pytestmark = pytest.mark.chaos


def test_forecast_survives_database_outage():
    """Cut database access, restart Weather, test fallback, and restore."""
    root = Path(__file__).resolve().parents[1]
    settings = Settings.from_file(root / ".env")
    aws = AwsClients.create(settings.region)
    outputs = StackOutputs.load(root)
    database = json.loads((root / "db_outputs.json").read_text())

    group_id = database["DB_SECURITY_GROUP_ID"]
    rule = database_ingress(settings)
    base = (
        f"http://{outputs.alb_dns_name}:"
        f"{settings.weather.listener_port}"
    )

    # Confirm the application is working before changing network access.
    response = requests.get(
        f"{base}/weather?zip=60616", timeout=70
    )
    assert response.status_code == 200
    assert response.json().get("cache") in {"hit", "miss"}

    try:
        aws.ec2.revoke_security_group_ingress(
            GroupId=group_id,
            IpPermissions=rule,
        )
        print("\nDatabase access rule temporarily removed.")
        print("In the WEATHER AWS browser terminal, run:")
        print("sudo systemctl restart weather")
        input(
            "After that command finishes, return HERE and press Enter: "
        )

        for number in (1, 2):
            started = time.monotonic()
            response = requests.get(
                f"{base}/weather?zip=60616", timeout=70
            )
            elapsed = time.monotonic() - started
            body = response.json()
            print(
                f"\nRequest {number}: HTTP {response.status_code}, "
                f"cache={body.get('cache')}, seconds={elapsed:.3f}"
            )
            assert response.status_code == 200
            assert "forecast" in body
            assert body.get("cache") == "unavailable"

        response = requests.get(f"{base}/history", timeout=15)
        print(
            f"\nHistory during outage: HTTP {response.status_code}, "
            f"body={response.text}"
        )
        assert response.status_code == 503
        assert response.json().get("error") == "database_unavailable"

    finally:
        with ignore_errors("InvalidPermission.Duplicate"):
            aws.ec2.authorize_security_group_ingress(
                GroupId=group_id,
                IpPermissions=rule,
            )
        print("\nDatabase access rule restored.")
        """Verify forecasts remain available during a database network outage."""

import json
import time
from pathlib import Path

import pytest
import requests

from stack.clients import AwsClients
from stack.config import Settings
from stack.database import database_ingress
from stack.outputs import StackOutputs
from stack.util import ignore_errors

pytestmark = pytest.mark.chaos


def test_forecast_survives_database_outage():
    """Cut database access, restart Weather, test fallback, and restore."""
    root = Path(__file__).resolve().parents[1]
    settings = Settings.from_file(root / ".env")
    aws = AwsClients.create(settings.region)
    outputs = StackOutputs.load(root)
    database = json.loads((root / "db_outputs.json").read_text())

    group_id = database["DB_SECURITY_GROUP_ID"]
    rule = database_ingress(settings)
    base = (
        f"http://{outputs.alb_dns_name}:"
        f"{settings.weather.listener_port}"
    )

    # Confirm the application is working before changing network access.
    response = requests.get(
        f"{base}/weather?zip=60616", timeout=70
    )
    assert response.status_code == 200
    assert response.json().get("cache") in {"hit", "miss"}

    try:
        aws.ec2.revoke_security_group_ingress(
            GroupId=group_id,
            IpPermissions=rule,
        )
        print("\nDatabase access rule temporarily removed.")
        print("In the WEATHER AWS browser terminal, run:")
        print("sudo systemctl restart weather")
        input(
            "After that command finishes, return HERE and press Enter: "
        )

        for number in (1, 2):
            started = time.monotonic()
            response = requests.get(
                f"{base}/weather?zip=60616", timeout=70
            )
            elapsed = time.monotonic() - started
            body = response.json()
            print(
                f"\nRequest {number}: HTTP {response.status_code}, "
                f"cache={body.get('cache')}, seconds={elapsed:.3f}"
            )
            assert response.status_code == 200
            assert "forecast" in body
            assert body.get("cache") == "unavailable"

        response = requests.get(f"{base}/history", timeout=15)
        print(
            f"\nHistory during outage: HTTP {response.status_code}, "
            f"body={response.text}"
        )
        assert response.status_code == 503
        assert response.json().get("error") == "database_unavailable"

    finally:
        with ignore_errors("InvalidPermission.Duplicate"):
            aws.ec2.authorize_security_group_ingress(
                GroupId=group_id,
                IpPermissions=rule,
            )
        print("\nDatabase access rule restored.")