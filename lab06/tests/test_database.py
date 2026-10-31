"""Database infrastructure tests using mocked AWS services."""

from types import SimpleNamespace

import boto3
from moto import mock_aws

from stack.database import ensure_database_security_group


@mock_aws
def test_database_ingress_is_only_from_app_group():
    """Port 5432 must allow the app group, with no CIDR-based access."""
    ec2 = boto3.client(
        "ec2",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    app_group = ec2.create_security_group(
        GroupName="test-app",
        Description="Application",
        VpcId=vpc_id,
    )["GroupId"]

    settings = SimpleNamespace(
        db_sg_name="test-weather-db",
        db_port=5432,
        app_sg_id=app_group,
    )
    aws = SimpleNamespace(ec2=ec2)

    group_id = ensure_database_security_group(aws, settings, vpc_id)
    second_id = ensure_database_security_group(aws, settings, vpc_id)
    assert second_id == group_id

    group = ec2.describe_security_groups(
        GroupIds=[group_id]
    )["SecurityGroups"][0]
    rules = group["IpPermissions"]

    assert len(rules) == 1
    rule = rules[0]
    assert rule["IpProtocol"] == "tcp"
    assert rule["FromPort"] == rule["ToPort"] == 5432
    assert [pair["GroupId"] for pair in rule["UserIdGroupPairs"]] == [
        app_group
    ]
    assert not rule.get("IpRanges")
    assert not rule.get("Ipv6Ranges")
    assert not rule.get("PrefixListIds")

    groups = ec2.describe_security_groups(Filters=[
        {"Name": "group-name", "Values": [settings.db_sg_name]},
        {"Name": "vpc-id", "Values": [vpc_id]},
    ])["SecurityGroups"]
    assert len(groups) == 1


@mock_aws
def test_password_is_secure_and_reused():
    """Repeated calls must reuse one SecureString password."""
    from stack.database import ensure_database_password

    options = {
        "region_name": "us-east-2",
        "aws_access_key_id": "testing",
        "aws_secret_access_key": "testing",
    }
    aws = SimpleNamespace(
        ssm=boto3.client("ssm", **options),
        rds=boto3.client("rds", **options),
    )
    settings = SimpleNamespace(
        db_password_param="/test/weather/password",
        db_instance_id="test-weather-db",
    )

    first = ensure_database_password(aws, settings)
    second = ensure_database_password(aws, settings)

    # Boolean assertions avoid displaying password values on failure.
    assert first == second, "Password changed on repeated creation"
    assert len(first) == 32, "Unexpected generated password length"
    assert all(c in "0123456789abcdef" for c in first)

    parameter = aws.ssm.get_parameter(
        Name=settings.db_password_param,
        WithDecryption=False,
    )["Parameter"]
    assert parameter["Type"] == "SecureString"
    assert parameter["Version"] == 1


@mock_aws
def test_database_create_twice_then_delete_twice():
    """Verify RDS settings, repeatable creation, and complete cleanup."""
    import pytest
    from botocore.exceptions import ClientError
    from stack.database import (
        delete_database_resources,
        ensure_database_instance,
        ensure_database_subnet_group,
    )

    options = {
        "region_name": "us-east-2",
        "aws_access_key_id": "testing",
        "aws_secret_access_key": "testing",
    }
    aws = SimpleNamespace(
        ec2=boto3.client("ec2", **options),
        rds=boto3.client("rds", **options),
        ssm=boto3.client("ssm", **options),
    )
    vpc_id = aws.ec2.create_vpc(
        CidrBlock="10.0.0.0/16"
    )["Vpc"]["VpcId"]

    subnets = []
    for zone, cidr in [
        ("us-east-2a", "10.0.1.0/24"),
        ("us-east-2b", "10.0.2.0/24"),
    ]:
        subnet = aws.ec2.create_subnet(
            VpcId=vpc_id,
            AvailabilityZone=zone,
            CidrBlock=cidr,
        )["Subnet"]
        subnets.append(subnet["SubnetId"])

    app_group = aws.ec2.create_security_group(
        GroupName="test-app",
        Description="Application",
        VpcId=vpc_id,
    )["GroupId"]

    settings = SimpleNamespace(
        db_instance_id="test-weather-db",
        db_instance_class="db.t4g.micro",
        db_allocated_storage=20,
        db_port=5432,
        db_name="weather",
        db_user="weatherapp",
        db_sg_name="test-db-sg",
        db_subnet_group_name="test-db-subnets",
        db_password_param="/test/weather/password",
        app_sg_id=app_group,
    )

    for _ in range(2):
        group_id = ensure_database_security_group(
            aws, settings, vpc_id
        )
        ensure_database_subnet_group(aws, settings, subnets)
        database = ensure_database_instance(aws, settings, group_id)

        assert database["DBInstanceClass"] == "db.t4g.micro"
        assert database["AllocatedStorage"] == 20
        assert database["BackupRetentionPeriod"] == 0
        assert database["MultiAZ"] is False
        assert database["PubliclyAccessible"] is False
        assert database["StorageEncrypted"] is True

    assert len(aws.rds.describe_db_instances()["DBInstances"]) == 1
    assert len(
        aws.rds.describe_db_subnet_groups()["DBSubnetGroups"]
    ) == 1
    parameter = aws.ssm.get_parameter(
        Name=settings.db_password_param
    )["Parameter"]
    assert parameter["Version"] == 1

    for _ in range(2):
        delete_database_resources(aws, settings, vpc_id)

    assert aws.rds.describe_db_instances()["DBInstances"] == []
    assert aws.rds.describe_db_snapshots()["DBSnapshots"] == []
    assert aws.rds.describe_db_subnet_groups()["DBSubnetGroups"] == []

    with pytest.raises(ClientError) as caught:
        aws.ssm.get_parameter(Name=settings.db_password_param)
    assert caught.value.response["Error"]["Code"] == "ParameterNotFound"

    remaining = aws.ec2.describe_security_groups(Filters=[
        {"Name": "group-name", "Values": [settings.db_sg_name]},
        {"Name": "vpc-id", "Values": [vpc_id]},
    ])["SecurityGroups"]
    assert remaining == []
