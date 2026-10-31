"""Startup configuration and launch-template tests."""

import base64
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from stack.compute import (
    build_user_data,
    ensure_launch_template,
    launch_template_data,
)
from stack.config import Tier


@pytest.fixture
def settings():
    """Provide dummy configuration with the real application source files."""
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

    return SimpleNamespace(
        region="us-east-2",
        app_port=3000,
        contact_email="student@example.com",
        db_port=5432,
        db_name="weather",
        db_user="weatherapp",
        db_password_param="/test/db-password",
        cache_seconds=600,
        gemini_model="test-model",
        gemini_param_name="/test/gemini-key",
        key_cache_seconds=60,
        ami_id="ami-12345678",
        instance_type="t3.micro",
        key_name="test-key",
        app_sg_id="sg-12345678",
        weather=tier("weather", 8080),
        advisor=tier("advisor", 8081),
    )


@pytest.mark.parametrize("name", ["weather", "advisor"])
def test_startup_configuration_and_size(settings, name):
    """Embed dependency addresses and parameter names, within EC2's limit."""
    tier = getattr(settings, name)
    alb = "test-alb.elb.amazonaws.com"
    host = "test-db.rds.amazonaws.com"
    script = build_user_data(settings, tier, alb, host)

    assert len(script.encode()) < 16384
    assert "DB_PASSWORD=" not in script
    assert "GEMINI_API_KEY=" not in script

    if name == "weather":
        assert f"DB_HOST={host}" in script
        assert settings.db_password_param in script
        assert settings.gemini_param_name not in script
        assert "global-bundle.pem" in script
    else:
        assert f"WEATHER_URL=http://{alb}:8080" in script
        assert settings.gemini_param_name in script
        assert settings.db_password_param not in script

    data = launch_template_data(settings, tier, alb, host)
    assert base64.b64decode(data["UserData"]).decode() == script
    assert data["MetadataOptions"]["HttpTokens"] == "required"


@pytest.mark.parametrize("name", ["weather", "advisor"])
@mock_aws
def test_repeated_creation_keeps_one_launch_template_version(settings, name):
    """An unchanged create call must not publish another template version."""
    aws = SimpleNamespace(ec2=boto3.client(
        "ec2",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    ))
    tier = getattr(settings, name)
    arguments = (
        aws, settings, tier,
        "test-alb.elb.amazonaws.com", "test-db.rds.amazonaws.com",
    )

    first = ensure_launch_template(*arguments)
    second = ensure_launch_template(*arguments)
    assert first == second == 1

    versions = aws.ec2.describe_launch_template_versions(
        LaunchTemplateName=tier.lt_name
    )["LaunchTemplateVersions"]
    assert len(versions) == 1

    templates = aws.ec2.describe_launch_templates(
        LaunchTemplateNames=[tier.lt_name]
    )["LaunchTemplates"]
    assert len(templates) == 1
