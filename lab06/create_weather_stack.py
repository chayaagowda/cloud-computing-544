"""Deploy the Weather and Advisor application infrastructure."""

import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from stack.clients import AwsClients
from stack.config import Settings
from stack.database import find_database
from stack.runner import create_stack


def main():
    """Validate database metadata, deploy both tiers, and save outputs."""
    directory = Path(__file__).resolve().parent
    settings = Settings.from_file(directory / ".env")
    aws = AwsClients.create(settings.region)

    output_path = directory / "db_outputs.json"
    if not output_path.exists():
        raise SystemExit(
            "Database outputs are missing. Run create_database.py first."
        )
    database_outputs = json.loads(output_path.read_text())

    database = find_database(aws, settings)
    if database is None or database["DBInstanceStatus"] != "available":
        raise SystemExit("The configured database must be available first.")

    host = database["Endpoint"]["Address"]
    if (
        database_outputs["DB_INSTANCE_ID"] != settings.db_instance_id
        or database_outputs["DB_HOST"] != host
    ):
        raise SystemExit(
            "Database outputs are stale. Rerun create_database.py."
        )

    account_id = boto3.client(
        "sts", region_name=settings.region
    ).get_caller_identity()["Account"]

    outputs = create_stack(settings, aws, account_id, host)
    outputs.save(directory)

    print("Application infrastructure created.")
    print(
        "Weather URL:",
        f"http://{outputs.alb_dns_name}:{settings.weather.listener_port}",
    )
    print(
        "Advisor URL:",
        f"http://{outputs.alb_dns_name}:{settings.advisor.listener_port}",
    )
    print("Instances may need several minutes to install dependencies.")
    print("Target health and application checks are still required.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        raise SystemExit(
            "AWS operation failed: " + error.response["Error"]["Code"]
        ) from None
