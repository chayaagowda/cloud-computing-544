"""Delete the Weather and Advisor application infrastructure."""

from pathlib import Path

from botocore.exceptions import ClientError

from stack.clients import AwsClients
from stack.config import Settings
from stack.outputs import StackOutputs
from stack.runner import delete_stack


def main():
    """Remove application resources and their local output files."""
    directory = Path(__file__).resolve().parent
    settings = Settings.from_file(directory / ".env")
    aws = AwsClients.create(settings.region)

    delete_stack(settings, aws)
    StackOutputs.remove(directory)

    print("Application outputs removed.")
    print("Database teardown: run delete_database.py separately.")
    print("The Gemini parameter remains until final secret cleanup.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        raise SystemExit(
            "AWS operation failed: " + error.response["Error"]["Code"]
        ) from None
