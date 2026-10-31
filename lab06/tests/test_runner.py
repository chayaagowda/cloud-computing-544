"""Test complete application-stack creation with mocked AWS."""

import base64
from pathlib import Path
from types import SimpleNamespace

from moto import mock_aws

from stack import compute
from stack.clients import AwsClients
from stack.config import Tier
from stack.runner import create_stack


@mock_aws
def test_stack_is_alb_first_and_repeatable(monkeypatch):
    """Create twice without duplicates and verify dependency injection."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    aws = AwsClients.create("us-east-2")
    vpcs = aws.ec2.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}]
    )["Vpcs"]
    if vpcs:
        vpc_id = vpcs[0]["VpcId"]
    else:
        vpc_id = aws.ec2.create_default_vpc()["Vpc"]["VpcId"]

    aws.ec2.create_key_pair(KeyName="test-key")

    image_id = aws.ec2.register_image(
        Name="test-linux-image",
        Architecture="x86_64",
        VirtualizationType="hvm",
        RootDeviceName="/dev/xvda",
    )["ImageId"]

    app_group = aws.ec2.create_security_group(
        GroupName="test-app",
        Description="Application",
        VpcId=vpc_id,
    )["GroupId"]

    root = Path(__file__).resolve().parents[1]

    def tier(name, port):
        return Tier(
            name=name,
            role_name=f"test-{name}-role",
            profile_name=f"test-{name}-profile",
            lt_name=f"test-{name}-lt",
            tg_name=f"test-{name}-tg",
            asg_name=f"test-{name}-asg",
            listener_port=port,
            app_dir=root / f"{name}-app",
        )

    weather = tier("weather", 8080)
    advisor = tier("advisor", 8081)

    settings = SimpleNamespace(
        region="us-east-2",
        ami_id=image_id,
        key_name="test-key",
        instance_type="t3.micro",
        app_sg_id=app_group,
        app_port=3000,
        alb_name="test-weather-alb",
        alb_sg_name="test-weather-alb-sg",
        az_a="us-east-2a",
        az_b="us-east-2b",
        contact_email="student@example.com",
        db_port=5432,
        db_name="weather",
        db_user="weatherapp",
        db_password_param="/test/db-password",
        cache_seconds=600,
        gemini_param_name="/test/gemini-key",
        gemini_model="test-model",
        key_cache_seconds=60,
        weather=weather,
        advisor=advisor,
        tiers=(weather, advisor),
    )

    for name in (
        settings.db_password_param,
        settings.gemini_param_name,
    ):
        aws.ssm.put_parameter(
            Name=name,
            Type="SecureString",
            Value="mock-only-value",
        )

    actual_ensure = compute.ensure_launch_template
    calls = []

    def check_alb_before_template(
        aws_arg, config, app_tier, dns, host
    ):
        load_balancers = aws_arg.elbv2.describe_load_balancers(
            Names=[config.alb_name]
        )["LoadBalancers"]

        assert len(load_balancers) == 1
        assert load_balancers[0]["DNSName"] == dns

        calls.append(app_tier.name)

        return actual_ensure(
            aws_arg, config, app_tier, dns, host
        )

    monkeypatch.setattr(
        compute,
        "ensure_launch_template",
        check_alb_before_template,
    )

    database_host = "test-db.rds.amazonaws.com"

    first = create_stack(
        settings,
        aws,
        "123456789012",
        database_host,
        iam_wait=0,
        sleep=lambda _: None,
    )

    second = create_stack(
        settings,
        aws,
        "123456789012",
        database_host,
        iam_wait=0,
        sleep=lambda _: None,
    )

    assert first == second
    assert calls == [
        "weather", "advisor", "weather", "advisor"
    ]

    assert len(
        aws.elbv2.describe_load_balancers()["LoadBalancers"]
    ) == 1

    assert len(
        aws.elbv2.describe_target_groups()["TargetGroups"]
    ) == 2

    assert len(
        aws.elbv2.describe_listeners(
            LoadBalancerArn=first.alb_arn
        )["Listeners"]
    ) == 2

    assert len(
        aws.autoscaling.describe_auto_scaling_groups()[
            "AutoScalingGroups"
        ]
    ) == 2

    assert len(aws.iam.list_roles()["Roles"]) == 2

    assert len(
        aws.iam.list_instance_profiles()["InstanceProfiles"]
    ) == 2

    assert len(
        aws.ec2.describe_launch_templates()["LaunchTemplates"]
    ) == 2

    for app_tier in settings.tiers:
        versions = aws.ec2.describe_launch_template_versions(
            LaunchTemplateName=app_tier.lt_name
        )["LaunchTemplateVersions"]

        assert len(versions) == 1

        script = base64.b64decode(
            versions[0]["LaunchTemplateData"]["UserData"]
        ).decode()

        assert "mock-only-value" not in script

        if app_tier.name == "advisor":
            assert (
                f"WEATHER_URL=http://{first.alb_dns_name}:8080"
            ) in script
        else:
            assert f"DB_HOST={database_host}" in script