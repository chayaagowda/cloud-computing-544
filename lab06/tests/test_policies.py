"""Check each tier's exact, least-privilege secret policy."""

import pytest

from stack.config import Settings
from stack.policies import parameter_arn, policy_for


@pytest.mark.parametrize(
    "tier,own,other",
    [
        ("weather", "/lab06/db-password", "/lab06/gemini-key"),
        ("advisor", "/lab06/gemini-key", "/lab06/db-password"),
    ],
)
def test_role_can_read_only_its_own_secret(tier, own, other):
    """Each policy must grant only GetParameter on its own secret."""
    from types import SimpleNamespace

    settings = SimpleNamespace(
        region="us-east-2",
        db_password_param="/lab06/db-password",
        gemini_param_name="/lab06/gemini-key",
    )
    policy = policy_for(tier, settings, "123456789012")

    assert policy == {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["ssm:GetParameter"],
            "Resource": parameter_arn(
                "us-east-2", "123456789012", own
            ),
        }],
    }
    assert other.lstrip("/") not in str(policy)


@pytest.mark.parametrize("name", ["", "/", "/lab06/*", "/lab06/key?"])
def test_parameter_arn_rejects_empty_or_wildcard_names(name):
    """Prevent policies from accidentally covering multiple secrets."""
    with pytest.raises(ValueError):
        parameter_arn("us-east-2", "123456789012", name)
