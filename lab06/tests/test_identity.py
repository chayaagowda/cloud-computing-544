"""Mocked IAM creation, least-privilege policy, and cleanup tests."""

from types import SimpleNamespace

import boto3
from moto import mock_aws

from stack.config import Tier
from stack.identity import ensure_role_and_profile, delete_role_and_profile
from stack.policies import policy_for


@mock_aws
def test_roles_profiles_and_policies_are_repeatable():
    """Create both roles twice, inspect their policies, then delete twice."""
    iam = boto3.client(
        "iam",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    aws = SimpleNamespace(iam=iam)
    settings = SimpleNamespace(
        region="us-east-2",
        db_password_param="/test/db-password",
        gemini_param_name="/test/gemini-key",
    )
    account_id = "123456789012"
    tiers = [
        Tier(
            name=name,
            role_name=f"test-{name}-role",
            profile_name=f"test-{name}-profile",
            lt_name="", tg_name="", asg_name="",
            listener_port=port,
            app_dir=None,
        )
        for name, port in [("weather", 8080), ("advisor", 8081)]
    ]

    for tier in tiers:
        for _ in range(2):
            ensure_role_and_profile(aws, settings, tier, account_id)

        stored = iam.get_role_policy(
            RoleName=tier.role_name,
            PolicyName=tier.policy_name,
        )["PolicyDocument"]
        assert stored == policy_for(tier.name, settings, account_id)

        profile = iam.get_instance_profile(
            InstanceProfileName=tier.profile_name
        )["InstanceProfile"]
        assert [r["RoleName"] for r in profile["Roles"]] == [
            tier.role_name
        ]
        assert iam.list_attached_role_policies(
            RoleName=tier.role_name
        )["AttachedPolicies"] == []
        assert iam.list_role_policies(
            RoleName=tier.role_name
        )["PolicyNames"] == [tier.policy_name]

    assert len(iam.list_roles()["Roles"]) == 2
    assert len(iam.list_instance_profiles()["InstanceProfiles"]) == 2

    for _ in range(2):
        for tier in tiers:
            delete_role_and_profile(aws, tier)

    assert iam.list_roles()["Roles"] == []
    assert iam.list_instance_profiles()["InstanceProfiles"] == []
