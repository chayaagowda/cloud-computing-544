"""Test a Gemini key and store it securely in SSM."""

import getpass
import warnings
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError

from stack.config import Settings


def main():
    """Read the key without echoing it, test it, and store a SecureString."""
    directory = Path(__file__).resolve().parent
    settings = Settings.from_file(directory / ".env")

    # Stop rather than fall back to an input method that echoes the key.
    warnings.simplefilter("error", getpass.GetPassWarning)
    key = getpass.getpass("Paste Gemini API key (hidden): ").strip()
    if not key:
        raise SystemExit("No key entered.")

    base = "https://generativelanguage.googleapis.com/v1beta"
    response = requests.post(
        f"{base}/models/{settings.gemini_model}:generateContent",
        headers={"x-goog-api-key": key},
        json={
            "contents": [{
                "parts": [{
                    "text": (
                        "Suggest what to wear in 45 F weather with light "
                        "rain, in one short sentence."
                    )
                }]
            }],
            "generationConfig": {"maxOutputTokens": 160},
        },
        timeout=20,
    )

    print("Gemini test HTTP status:", response.status_code)

    if response.status_code == 404:
        listing = requests.get(
            f"{base}/models",
            headers={"x-goog-api-key": key},
            timeout=20,
        )
        if listing.ok:
            print("Available Flash models supporting generateContent:")
            for model in listing.json().get("models", []):
                if (
                    "flash" in model.get("name", "").lower()
                    and "generateContent"
                    in model.get("supportedGenerationMethods", [])
                ):
                    print(model["name"].removeprefix("models/"))
        raise SystemExit(
            "Model unavailable. Send the model list, never your key."
        )

    if not response.ok:
        raise SystemExit(
            "Gemini test failed; key was not stored. "
            "Send only the HTTP status."
        )

    data = response.json()
    parts = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    answer = "".join(part.get("text", "") for part in parts).strip()
    if not answer:
        raise SystemExit("Gemini returned no text; key was not stored.")

    print("Test suggestion:", answer)

    ssm = boto3.client("ssm", region_name=settings.region)
    ssm.put_parameter(
        Name=settings.gemini_param_name,
        Type="SecureString",
        Tier="Standard",
        Value=key,
        Overwrite=True,
    )
    del key

    metadata = ssm.get_parameter(
        Name=settings.gemini_param_name,
        WithDecryption=False,
    )["Parameter"]
    print("Stored parameter:", metadata["Name"])
    print("Parameter type:", metadata["Type"])
    print("Key value was not printed or saved to a local file.")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException:
        raise SystemExit(
            "Gemini network request failed; request details hidden."
        ) from None
    except ClientError as error:
        raise SystemExit(
            "AWS operation failed: " + error.response["Error"]["Code"]
        ) from None