"""Delete the Lab06 database resources and local connection metadata."""

from pathlib import Path

from botocore.exceptions import ClientError

from stack.clients import AwsClients
from stack.config import Settings
from stack.database import delete_database_resources
from stack.network import default_vpc_id


def main():
    """Remove the configured database without a final snapshot."""
    directory = Path(__file__).resolve().parent
    settings = Settings.from_file(directory / ".env")
    aws = AwsClients.create(settings.region)

    print("Deleting database without a final snapshot or retained backups...")
    delete_database_resources(
        aws, settings, default_vpc_id(aws)
    )

    for name in ("db_outputs.json", "db_outputs.env"):
        (directory / name).unlink(missing_ok=True)

    print("Database resource cleanup completed.")
    print("Separate snapshot and backup verification is still required.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        raise SystemExit(
            "AWS operation failed: " + error.response["Error"]["Code"]
        ) from None
