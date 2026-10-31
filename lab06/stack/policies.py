"""Least-privilege IAM policy documents for Lab06."""


def trust_policy() -> dict:
    """Allow EC2 to assume an application role."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }


def parameter_arn(
    region: str,
    account_id: str,
    parameter_name: str,
    partition: str = "aws",
) -> str:
    """Build an exact SSM parameter ARN without a wildcard."""
    name = parameter_name.lstrip("/")
    if not name or any(char in name for char in "*?"):
        raise ValueError("An exact parameter name is required")
    return f"arn:{partition}:ssm:{region}:{account_id}:parameter/{name}"


def secret_read_policy(arn: str) -> dict:
    """Allow reading exactly one parameter."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["ssm:GetParameter"],
            "Resource": arn,
        }],
    }


def policy_for(tier_name: str, settings, account_id: str) -> dict:
    """Give each tier access only to its own secret."""
    names = {
        "weather": settings.db_password_param,
        "advisor": settings.gemini_param_name,
    }
    arn = parameter_arn(
        settings.region, account_id, names[tier_name]
    )
    return secret_read_policy(arn)
