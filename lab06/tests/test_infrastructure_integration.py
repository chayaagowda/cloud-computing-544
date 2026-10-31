"""Verify the deployed infrastructure using real AWS and network checks."""

import socket
from pathlib import Path

import pytest
import requests

from stack.clients import AwsClients
from stack.config import Settings
from stack.outputs import StackOutputs
from stack.policies import parameter_arn

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def deployed():
    """Load the deployed stack without creating or deleting resources."""
    root = Path(__file__).resolve().parents[1]
    settings = Settings.from_file(root / ".env")
    return (
        settings,
        AwsClients.create(settings.region),
        StackOutputs.load(root),
    )


@pytest.mark.parametrize("name", ["weather", "advisor"])
def test_target_group_is_healthy(deployed, name):
    """Each tier must have one healthy target."""
    settings, aws, outputs = deployed
    targets = aws.elbv2.describe_target_health(
        TargetGroupArn=outputs.tg_arn(name)
    )["TargetHealthDescriptions"]

    assert len(targets) == 1
    assert targets[0]["Target"]["Port"] == settings.app_port
    assert targets[0]["TargetHealth"]["State"] == "healthy"


@pytest.mark.parametrize(
    "name,secret,allowed",
    [
        ("weather", "database", True),
        ("weather", "gemini", False),
        ("advisor", "database", False),
        ("advisor", "gemini", True),
    ],
)
def test_secret_isolation(deployed, name, secret, allowed):
    """IAM simulation must permit only each tier's own secret."""
    settings, aws, _ = deployed
    tier = settings.tier(name)
    role_arn = aws.iam.get_role(
        RoleName=tier.role_name
    )["Role"]["Arn"]
    account = role_arn.split(":")[4]
    parameter = (
        settings.db_password_param
        if secret == "database"
        else settings.gemini_param_name
    )

    result = aws.iam.simulate_principal_policy(
        PolicySourceArn=role_arn,
        ActionNames=["ssm:GetParameter"],
        ResourceArns=[
            parameter_arn(settings.region, account, parameter)
        ],
    )["EvaluationResults"][0]["EvalDecision"]

    if allowed:
        assert result == "allowed"
    else:
        assert result in {"implicitDeny", "explicitDeny"}


def test_application_security_group_allows_only_alb(deployed):
    """Every inbound rule covering the app port must name only the ALB."""
    settings, aws, outputs = deployed
    group = aws.ec2.describe_security_groups(
        GroupIds=[settings.app_sg_id]
    )["SecurityGroups"][0]

    matching = []
    for rule in group["IpPermissions"]:
        protocol = rule["IpProtocol"]
        if protocol == "-1":
            pytest.fail("An all-protocol inbound rule exposes the app port.")
        if (
            protocol in {"tcp", "6"}
            and rule.get("FromPort", 0) <= settings.app_port
            <= rule.get("ToPort", 65535)
        ):
            matching.append(rule)

    assert len(matching) == 1
    rule = matching[0]
    assert rule["FromPort"] == rule["ToPort"] == settings.app_port
    assert not rule.get("IpRanges")
    assert not rule.get("Ipv6Ranges")
    assert not rule.get("PrefixListIds")
    assert [p["GroupId"] for p in rule["UserIdGroupPairs"]] == [
        outputs.alb_security_group_id
    ]


@pytest.mark.parametrize("name", ["weather", "advisor"])
def test_application_reachable_through_alb_only(deployed, name):
    """Prove ALB access works while direct public app-port access fails."""
    settings, aws, outputs = deployed
    tier = settings.tier(name)
    url = f"http://{outputs.alb_dns_name}:{tier.listener_port}/"
    assert requests.get(url, timeout=15).status_code == 200

    targets = aws.elbv2.describe_target_health(
        TargetGroupArn=outputs.tg_arn(name)
    )["TargetHealthDescriptions"]
    assert len(targets) == 1
    instance_id = targets[0]["Target"]["Id"]
    instance = aws.ec2.describe_instances(
        InstanceIds=[instance_id]
    )["Reservations"][0]["Instances"][0]
    address = instance.get("PublicIpAddress")
    assert address, "A public IP is needed for this direct-access check."

    try:
        connection = socket.create_connection(
            (address, settings.app_port), timeout=5
        )
    except (TimeoutError, ConnectionRefusedError):
        return

    connection.close()
    pytest.fail(f"{name} app port was directly reachable.")


def test_database_configuration(deployed):
    """Verify the real RDS instance meets the assignment settings."""
    settings, aws, _ = deployed
    database = aws.rds.describe_db_instances(
        DBInstanceIdentifier=settings.db_instance_id
    )["DBInstances"][0]

    assert database["DBInstanceStatus"] == "available"
    assert database["DBInstanceClass"] == "db.t4g.micro"
    assert database["AllocatedStorage"] == 20
    assert database["StorageEncrypted"] is True
    assert database["MultiAZ"] is False
    assert database["PubliclyAccessible"] is False
    assert database["BackupRetentionPeriod"] == 0


def test_database_port_unreachable_from_this_computer(deployed):
    """Resolve the real endpoint, then verify TCP access is blocked."""
    settings, aws, _ = deployed
    database = aws.rds.describe_db_instances(
        DBInstanceIdentifier=settings.db_instance_id
    )["DBInstances"][0]
    endpoint = database["Endpoint"]

    # A DNS failure must not count as successful network isolation.
    socket.getaddrinfo(endpoint["Address"], endpoint["Port"])

    try:
        connection = socket.create_connection(
            (endpoint["Address"], endpoint["Port"]), timeout=5
        )
    except (TimeoutError, ConnectionRefusedError):
        return

    connection.close()
    pytest.fail("Database port was reachable from this computer.")