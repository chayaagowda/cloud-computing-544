"""Verify each application role's SSM permissions using IAM simulation."""

import boto3
from botocore.exceptions import ClientError

from stack.config import Settings
from stack.policies import parameter_arn


def main():
    settings = Settings.from_file()
    session = boto3.Session(region_name=settings.region)
    iam = session.client("iam")
    account = session.client("sts").get_caller_identity()["Account"]

    secrets = {
        "database": settings.db_password_param,
        "gemini": settings.gemini_param_name,
    }

    passed = True
    for tier in settings.tiers:
        role_arn = iam.get_role(
            RoleName=tier.role_name
        )["Role"]["Arn"]

        for label, name in secrets.items():
            result = iam.simulate_principal_policy(
                PolicySourceArn=role_arn,
                ActionNames=["ssm:GetParameter"],
                ResourceArns=[
                    parameter_arn(settings.region, account, name)
                ],
            )["EvaluationResults"][0]

            decision = result["EvalDecision"]
            should_allow = (
                (tier.name == "weather" and label == "database")
                or (tier.name == "advisor" and label == "gemini")
            )
            correct = (
                decision == "allowed"
                if should_allow
                else decision in {"implicitDeny", "explicitDeny"}
            )
            passed = passed and correct

            print(
                f"{tier.name:8} | {label:8} | "
                f"{decision:12} | {'PASS' if correct else 'FAIL'}"
            )

    if not passed:
        raise SystemExit("Secret permission checks failed.")
    print("All four secret permission checks passed.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        print("Operation:", error.operation_name)
        print("Message:", error.response["Error"]["Message"])
        raise SystemExit(1) from None