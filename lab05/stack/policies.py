"""IAM policy documents as plain Python dictionaries."""
from __future__ import annotations

OBJECT_KEY = "shared.txt"


def trust_policy() -> dict:
    """Allow the EC2 service to assume the role."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }


def uploader_policy(bucket: str) -> dict:
    """Allow writing only shared.txt."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["s3:PutObject"],
            "Resource": f"arn:aws:s3:::{bucket}/{OBJECT_KEY}",
        }],
    }


def viewer_policy(bucket: str) -> dict:
    """Allow reading shared.txt and listing the bucket."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": f"arn:aws:s3:::{bucket}/{OBJECT_KEY}",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": f"arn:aws:s3:::{bucket}",
            },
        ],
    }


def policy_for(tier_name: str, bucket: str) -> dict:
    return {
        "uploader": uploader_policy,
        "viewer": viewer_policy,
    }[tier_name](bucket)
