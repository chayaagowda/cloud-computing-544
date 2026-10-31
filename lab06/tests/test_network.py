"""Application security-group isolation tests."""

from types import SimpleNamespace

import boto3
from moto import mock_aws

from stack.network import (
    restrict_app_security_group,
    restore_app_security_group,
)


@mock_aws
def test_app_port_allows_only_alb_and_cleanup_keeps_it_closed():
    """Replace public app access with ALB access and preserve separate SSH."""
    ec2 = boto3.client(
        "ec2",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]

    def group(name):
        return ec2.create_security_group(
            GroupName=name, Description=name, VpcId=vpc
        )["GroupId"]

    app_group = group("test-app")
    alb_group = group("test-alb")
    for port in (22, 3000):
        ec2.authorize_security_group_ingress(
            GroupId=app_group,
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": port,
                "ToPort": port,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }],
        )

    aws = SimpleNamespace(ec2=ec2)
    settings = SimpleNamespace(app_sg_id=app_group, app_port=3000)

    for _ in range(2):
        restrict_app_security_group(aws, settings, alb_group)

    def rules():
        return ec2.describe_security_groups(
            GroupIds=[app_group]
        )["SecurityGroups"][0]["IpPermissions"]

    app_rules = [r for r in rules() if r.get("FromPort") == 3000]
    assert len(app_rules) == 1
    assert not app_rules[0].get("IpRanges")
    assert not app_rules[0].get("Ipv6Ranges")
    assert [p["GroupId"] for p in app_rules[0]["UserIdGroupPairs"]] == [
        alb_group
    ]
    assert any(r.get("FromPort") == 22 for r in rules())

    for _ in range(2):
        restore_app_security_group(aws, settings, alb_group)

    assert not any(r.get("FromPort") == 3000 for r in rules())
    assert any(r.get("FromPort") == 22 for r in rules())
