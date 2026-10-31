"""Create the Lab06 database and save non-secret connection metadata."""

import json
from pathlib import Path

from botocore.exceptions import ClientError

from stack.clients import AwsClients
from stack.config import Settings
from stack.database import (
    ensure_database_instance,
    ensure_database_security_group,
    ensure_database_subnet_group,
)
from stack.network import default_vpc_id, subnet_in_az


def main():
    """Build database resources using the configured AWS region."""
    directory = Path(__file__).resolve().parent
    settings = Settings.from_file(directory / ".env")
    aws = AwsClients.create(settings.region)

    vpc_id = default_vpc_id(aws)
    subnets = [
        subnet_in_az(aws, vpc_id, settings.az_a),
        subnet_in_az(aws, vpc_id, settings.az_b),
    ]

    app_group = aws.ec2.describe_security_groups(
        GroupIds=[settings.app_sg_id]
    )["SecurityGroups"][0]
    if app_group["VpcId"] != vpc_id:
        raise ValueError("Application security group is in another VPC.")

    print("Preparing database security group and subnet group...")
    group_id = ensure_database_security_group(aws, settings, vpc_id)
    ensure_database_subnet_group(aws, settings, subnets)

    print("Creating or reusing PostgreSQL; waiting until available...")
    database = ensure_database_instance(aws, settings, group_id)

    outputs = {
        "DB_INSTANCE_ID": settings.db_instance_id,
        "DB_HOST": database["Endpoint"]["Address"],
        "DB_PORT": database["Endpoint"]["Port"],
        "DB_NAME": settings.db_name,
        "DB_USER": settings.db_user,
        "DB_SECURITY_GROUP_ID": group_id,
        "DB_SUBNET_GROUP_NAME": settings.db_subnet_group_name,
        "DB_PASSWORD_PARAM": settings.db_password_param,
    }

    (directory / "db_outputs.json").write_text(
        json.dumps(outputs, indent=2) + "\n"
    )
    (directory / "db_outputs.env").write_text(
        "\n".join(f"{key}={value}" for key, value in outputs.items()) + "\n"
    )

    print("Database available.")
    print("Host:", outputs["DB_HOST"])
    print("Non-secret connection metadata saved.")
    print("No password was written to the output files.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        # Avoid printing request details that might include a secret.
        raise SystemExit(
            "AWS operation failed: " + error.response["Error"]["Code"]
        ) from None
