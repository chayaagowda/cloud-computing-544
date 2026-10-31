"""Remove the lab's Gemini parameter and verify AWS cleanup."""

import json

from botocore.exceptions import ClientError

from stack.clients import AwsClients
from stack.config import Settings


def main():
    """Check lab resources without printing secret values."""
    settings = Settings.from_file()
    aws = AwsClients.create(settings.region)

    try:
        aws.ssm.delete_parameter(Name=settings.gemini_param_name)
        print("Gemini parameter deleted.")
    except ClientError as error:
        if error.response["Error"]["Code"] != "ParameterNotFound":
            raise
        print("Gemini parameter was already absent.")

    def collect(client, operation, key):
        if client.can_paginate(operation):
            return [
                item
                for page in client.get_paginator(operation).paginate()
                for item in page[key]
            ]
        return getattr(client, operation)()[key]

    remaining = {}

    databases = collect(aws.rds, "describe_db_instances", "DBInstances")
    remaining["database_instances"] = [
        item["DBInstanceIdentifier"] for item in databases
        if item["DBInstanceIdentifier"] == settings.db_instance_id
    ]

    snapshots = collect(aws.rds, "describe_db_snapshots", "DBSnapshots")
    remaining["database_snapshots"] = [
        item["DBSnapshotIdentifier"] for item in snapshots
        if item.get("DBInstanceIdentifier") == settings.db_instance_id
    ]

    backups = collect(
        aws.rds,
        "describe_db_instance_automated_backups",
        "DBInstanceAutomatedBackups",
    )
    remaining["automated_backups"] = [
        item["DBInstanceAutomatedBackupsArn"] for item in backups
        if item.get("DBInstanceIdentifier") == settings.db_instance_id
    ]

    subnets = collect(
        aws.rds, "describe_db_subnet_groups", "DBSubnetGroups"
    )
    remaining["database_subnet_groups"] = [
        item["DBSubnetGroupName"] for item in subnets
        if item["DBSubnetGroupName"] == settings.db_subnet_group_name
    ]

    resource_checks = [
        (
            "load_balancers", aws.elbv2,
            "describe_load_balancers", "LoadBalancers",
            "LoadBalancerName", {settings.alb_name},
        ),
        (
            "target_groups", aws.elbv2,
            "describe_target_groups", "TargetGroups",
            "TargetGroupName", {tier.tg_name for tier in settings.tiers},
        ),
        (
            "auto_scaling_groups", aws.autoscaling,
            "describe_auto_scaling_groups", "AutoScalingGroups",
            "AutoScalingGroupName",
            {tier.asg_name for tier in settings.tiers},
        ),
        (
            "launch_templates", aws.ec2,
            "describe_launch_templates", "LaunchTemplates",
            "LaunchTemplateName", {tier.lt_name for tier in settings.tiers},
        ),
        (
            "application_roles", aws.iam,
            "list_roles", "Roles",
            "RoleName", {tier.role_name for tier in settings.tiers},
        ),
        (
            "instance_profiles", aws.iam,
            "list_instance_profiles", "InstanceProfiles",
            "InstanceProfileName",
            {tier.profile_name for tier in settings.tiers},
        ),
    ]

    for label, client, operation, key, field, names in resource_checks:
        remaining[label] = [
            item[field]
            for item in collect(client, operation, key)
            if item[field] in names
        ]

    groups = aws.ec2.describe_security_groups(Filters=[{
        "Name": "group-name",
        "Values": [settings.alb_sg_name, settings.db_sg_name],
    }])["SecurityGroups"]
    remaining["lab_security_groups"] = [
        group["GroupId"] for group in groups
    ]

    reservations = aws.ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": ["weather-app", "advisor-app"]},
        {
            "Name": "instance-state-name",
            "Values": ["pending", "running", "stopping", "stopped"],
        },
    ])["Reservations"]
    remaining["active_or_stopped_app_instances"] = [
        instance["InstanceId"]
        for reservation in reservations
        for instance in reservation["Instances"]
    ]

    remaining["secret_parameters"] = []
    for name in (
        settings.db_password_param,
        settings.gemini_param_name,
    ):
        try:
            aws.ssm.get_parameter(Name=name, WithDecryption=False)
        except ClientError as error:
            if error.response["Error"]["Code"] != "ParameterNotFound":
                raise
        else:
            remaining["secret_parameters"].append(name)

    print(json.dumps(remaining, indent=2))
    if any(remaining.values()):
        raise SystemExit("Cleanup incomplete: inspect the non-empty lists.")

    print("PASS: all checked Lab06 resources are absent.")


if __name__ == "__main__":
    main()