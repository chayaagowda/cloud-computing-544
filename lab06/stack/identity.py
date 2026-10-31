"""Manage application IAM roles and instance profiles."""

import json

from botocore.exceptions import ClientError

from .policies import policy_for, trust_policy
from .util import error_code, ignore_errors, retry_on


def ensure_role_and_profile(aws, settings, tier, account_id: str) -> None:
    """Create or reuse a role and profile with one secret-read policy."""
    try:
        aws.iam.get_role(RoleName=tier.role_name)
    except ClientError as error:
        if error_code(error) != "NoSuchEntity":
            raise
        with ignore_errors("EntityAlreadyExists"):
            aws.iam.create_role(
                RoleName=tier.role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy()),
            )

    aws.iam.put_role_policy(
        RoleName=tier.role_name,
        PolicyName=tier.policy_name,
        PolicyDocument=json.dumps(
            policy_for(tier.name, settings, account_id)
        ),
    )

    try:
        profile = aws.iam.get_instance_profile(
            InstanceProfileName=tier.profile_name
        )["InstanceProfile"]
    except ClientError as error:
        if error_code(error) != "NoSuchEntity":
            raise
        with ignore_errors("EntityAlreadyExists"):
            aws.iam.create_instance_profile(
                InstanceProfileName=tier.profile_name
            )
        profile = retry_on(
            {"NoSuchEntity"},
            lambda: aws.iam.get_instance_profile(
                InstanceProfileName=tier.profile_name
            )["InstanceProfile"],
            attempts=6,
            delay=5,
        )

    roles = [role["RoleName"] for role in profile["Roles"]]
    if roles and roles != [tier.role_name]:
        raise RuntimeError("Instance profile contains an unexpected role.")
    if not roles:
        retry_on(
            {"NoSuchEntity"},
            lambda: aws.iam.add_role_to_instance_profile(
                InstanceProfileName=tier.profile_name,
                RoleName=tier.role_name,
            ),
            attempts=6,
            delay=5,
        )


def delete_role_and_profile(aws, tier) -> None:
    """Remove lab-owned IAM resources, tolerating already absent resources."""
    with ignore_errors("NoSuchEntity"):
        aws.iam.remove_role_from_instance_profile(
            InstanceProfileName=tier.profile_name,
            RoleName=tier.role_name,
        )

    with ignore_errors("NoSuchEntity"):
        retry_on(
            {"DeleteConflict"},
            lambda: aws.iam.delete_instance_profile(
                InstanceProfileName=tier.profile_name
            ),
            attempts=6,
            delay=5,
        )

    with ignore_errors("NoSuchEntity"):
        aws.iam.delete_role_policy(
            RoleName=tier.role_name,
            PolicyName=tier.policy_name,
        )

    with ignore_errors("NoSuchEntity"):
        retry_on(
            {"DeleteConflict"},
            lambda: aws.iam.delete_role(RoleName=tier.role_name),
            attempts=6,
            delay=5,
        )
