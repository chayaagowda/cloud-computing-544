"""Build the boto3 clients needed by the stack."""

from __future__ import annotations

from dataclasses import dataclass

import boto3


@dataclass
class AwsClients:
    """AWS service clients shared by infrastructure functions."""

    s3: object
    iam: object
    ec2: object
    elbv2: object
    autoscaling: object
    rds: object
    ssm: object

    @classmethod
    def create(cls, region: str) -> "AwsClients":
        """Create service clients in the configured AWS region."""
        session = boto3.session.Session(region_name=region)
        return cls(
            s3=session.client("s3"),
            iam=session.client("iam"),
            ec2=session.client("ec2"),
            elbv2=session.client("elbv2"),
            autoscaling=session.client("autoscaling"),
            rds=session.client("rds"),
            ssm=session.client("ssm"),
        )
