"""Temporarily replace the Gemini key and verify labelled fallback."""

import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stack.clients import AwsClients
from stack.config import Settings
from stack.outputs import StackOutputs


def main():
    """Keep the real key in memory and restore it after the experiment."""
    settings = Settings.from_file(ROOT / ".env")
    aws = AwsClients.create(settings.region)
    outputs = StackOutputs.load(ROOT)

    original = aws.ssm.get_parameter(
        Name=settings.gemini_param_name,
        WithDecryption=True,
    )["Parameter"]["Value"]

    url = (
        f"http://{outputs.alb_dns_name}:"
        f"{settings.advisor.listener_port}/advice?zip=60616"
    )

    try:
        aws.ssm.put_parameter(
            Name=settings.gemini_param_name,
            Type="SecureString",
            Value="deliberately-invalid-lab06-key",
            Overwrite=True,
        )
        print("Temporary invalid key stored.")
        wait = settings.key_cache_seconds + 5
        print(f"Waiting {wait} seconds for the cached key to expire...")
        time.sleep(wait)

        response = requests.get(url, timeout=90)
        body = response.json()

        print("HTTP:", response.status_code)
        print("Source:", body.get("source"))
        print("Advice:", body.get("advice"))

        assert response.status_code == 200
        assert body.get("source") == "fallback"
        assert body.get("advice")
        print("PASS: invalid key produced labelled fallback advice.")

    finally:
        aws.ssm.put_parameter(
            Name=settings.gemini_param_name,
            Type="SecureString",
            Value=original,
            Overwrite=True,
        )
        del original
        print("Original Gemini key restored.")
        print("Advisor may retain the invalid key for another cache period.")


if __name__ == "__main__":
    main()