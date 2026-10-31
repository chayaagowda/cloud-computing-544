"""Generate application startup scripts and manage launch templates."""

import base64
import json
import shlex

from botocore.exceptions import ClientError

from .util import error_code


def build_user_data(settings, tier, alb_dns: str, db_host: str) -> str:
    """Embed app code and non-secret configuration in EC2 user-data."""
    environment = {
        "AWS_REGION": settings.region,
        "PORT": str(settings.app_port),
    }
    if tier.name == "weather":
        environment.update({
            "NWS_USER_AGENT": (
                f"(ITMO 544 weather lab, {settings.contact_email})"
            ),
            "DB_HOST": db_host,
            "DB_PORT": str(settings.db_port),
            "DB_NAME": settings.db_name,
            "DB_USER": settings.db_user,
            "DB_PASSWORD_PARAM": settings.db_password_param,
            "DB_CA_FILE": "/opt/app/rds-ca.pem",
            "CACHE_SECONDS": str(settings.cache_seconds),
        })
    elif tier.name == "advisor":
        environment.update({
            "WEATHER_URL": (
                f"http://{alb_dns}:{settings.weather.listener_port}"
            ),
            "GEMINI_MODEL": settings.gemini_model,
            "GEMINI_KEY_PARAM": settings.gemini_param_name,
            "KEY_CACHE_SECONDS": str(settings.key_cache_seconds),
        })
    else:
        raise ValueError("Unknown application tier")

    # systemd expands percent specifiers, so escape literal percent signs.
    env_lines = "\n".join(
        "Environment=" + json.dumps(
            f"{key}={value}", ensure_ascii=False
        ).replace("%", "%%")
        for key, value in environment.items()
    )

    files = []
    for name in ("app.js", "package.json"):
        encoded = base64.b64encode(
            (tier.app_dir / name).read_bytes()
        ).decode()
        files.append(
            f"printf '%s' '{encoded}' | base64 -d > /opt/app/{name}"
        )
    write_files = "\n".join(files)
    service = tier.service_name
    if service not in {"weather", "advisor"}:
        raise ValueError("Invalid service name")

    certificate = ""
    if tier.name == "weather":
        certificate = (
            "curl --fail --silent --show-error --retry 5 "
            "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem "
            "-o /opt/app/rds-ca.pem"
        )

    script = f"""#!/bin/bash
set -euo pipefail
dnf install -y nodejs npm
mkdir -p /opt/app
{write_files}
{certificate}
cd /opt/app
npm install --omit=dev --no-audit --no-fund
cat > /etc/systemd/system/{service}.service <<'SERVICE'
[Unit]
Description={service.capitalize()} application
Wants=network-online.target
After=network-online.target

[Service]
{env_lines}
WorkingDirectory=/opt/app
ExecStart=/usr/bin/node /opt/app/app.js
Restart=always
RestartSec=2
User=ec2-user

[Install]
WantedBy=multi-user.target
SERVICE
chown -R ec2-user:ec2-user /opt/app
systemctl daemon-reload
systemctl enable --now {shlex.quote(service)}.service
"""
    if len(script.encode()) >= 16384:
        raise ValueError(f"{tier.name} user-data must be under 16 KB")
    return script


def launch_template_data(settings, tier, alb_dns: str, db_host: str) -> dict:
    """Build a launch template without including secret values."""
    script = build_user_data(settings, tier, alb_dns, db_host)
    return {
        "ImageId": settings.ami_id,
        "InstanceType": settings.instance_type,
        "KeyName": settings.key_name,
        "SecurityGroupIds": [settings.app_sg_id],
        "IamInstanceProfile": {"Name": tier.profile_name},
        "MetadataOptions": {
            "HttpEndpoint": "enabled",
            "HttpTokens": "required",
        },
        "UserData": base64.b64encode(script.encode()).decode(),
        "TagSpecifications": [{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": f"{tier.name}-app"}],
        }],
    }


def latest_launch_template(aws, tier) -> dict | None:
    """Find the latest version, returning None when the template is absent."""
    try:
        return aws.ec2.describe_launch_template_versions(
            LaunchTemplateName=tier.lt_name,
            Versions=["$Latest"],
        )["LaunchTemplateVersions"][0]
    except ClientError as error:
        if error_code(error) != "InvalidLaunchTemplateName.NotFoundException":
            raise
        return None


def ensure_launch_template(
    aws, settings, tier, alb_dns: str, db_host: str
) -> int:
    """Create a template; create a new version only when its data changes."""
    data = launch_template_data(settings, tier, alb_dns, db_host)
    latest = latest_launch_template(aws, tier)
    if latest is None:
        result = aws.ec2.create_launch_template(
            LaunchTemplateName=tier.lt_name,
            LaunchTemplateData=data,
        )
        return result["LaunchTemplate"]["LatestVersionNumber"]

    existing = latest["LaunchTemplateData"]
    if all(existing.get(key) == value for key, value in data.items()):
        return latest["VersionNumber"]

    result = aws.ec2.create_launch_template_version(
        LaunchTemplateName=tier.lt_name,
        SourceVersion=str(latest["VersionNumber"]),
        LaunchTemplateData=data,
    )
    return result["LaunchTemplateVersion"]["VersionNumber"]


def delete_launch_template(aws, tier) -> None:
    """Delete a template, tolerating an already absent resource."""
    try:
        aws.ec2.delete_launch_template(LaunchTemplateName=tier.lt_name)
    except ClientError as error:
        if error_code(error) != "InvalidLaunchTemplateName.NotFoundException":
            raise
