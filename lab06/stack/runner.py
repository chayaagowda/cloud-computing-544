"""Create and delete the two-tier Weather Advisor infrastructure."""

import time

from . import balancer, compute, identity, network, scaling
from .outputs import StackOutputs
from .util import ignore_errors, retry_on


def create_stack(
    settings,
    aws,
    account_id: str,
    db_host: str,
    iam_wait: int = 15,
    sleep=time.sleep,
) -> StackOutputs:
    """Create the ALB before templates, then launch both application tiers."""
    if not db_host:
        raise ValueError("A database host is required.")

    # Verify secret metadata without requesting decrypted values.
    for name in (
        settings.db_password_param,
        settings.gemini_param_name,
    ):
        parameter = aws.ssm.get_parameter(
            Name=name, WithDecryption=False
        )["Parameter"]
        if parameter["Type"] != "SecureString":
            raise ValueError("Both application secrets must be SecureStrings.")

    vpc_id = network.default_vpc_id(aws)
    subnets = [
        network.subnet_in_az(aws, vpc_id, settings.az_a),
        network.subnet_in_az(aws, vpc_id, settings.az_b),
    ]

    app_group = aws.ec2.describe_security_groups(
        GroupIds=[settings.app_sg_id]
    )["SecurityGroups"][0]
    if app_group["VpcId"] != vpc_id:
        raise ValueError("Application security group is in another VPC.")

    print("Preparing network and load balancer...")
    alb_group = network.ensure_alb_security_group(
        aws, settings, vpc_id
    )
    network.restrict_app_security_group(aws, settings, alb_group)

    target_groups = {
        tier.name: balancer.ensure_target_group(
            aws, settings, tier, vpc_id
        )
        for tier in settings.tiers
    }

    alb_arn, alb_dns = balancer.ensure_load_balancer(
        aws, settings, subnets, alb_group
    )
    aws.elbv2.get_waiter("load_balancer_available").wait(
        LoadBalancerArns=[alb_arn]
    )
    for tier in settings.tiers:
        balancer.ensure_listener(
            aws, alb_arn, tier.listener_port, target_groups[tier.name]
        )

    print("Preparing IAM roles and instance profiles...")
    for tier in settings.tiers:
        identity.ensure_role_and_profile(
            aws, settings, tier, account_id
        )
    sleep(iam_wait)

    print("Preparing launch templates with dependency addresses...")
    for tier in settings.tiers:
        compute.ensure_launch_template(
            aws, settings, tier, alb_dns, db_host
        )

    print("Preparing Auto Scaling Groups...")
    for tier in settings.tiers:
        retry_on(
            {"ValidationError", "InvalidIamInstanceProfile"},
            lambda tier=tier: scaling.ensure_asg(
                aws, tier, target_groups[tier.name], subnets
            ),
            attempts=6,
            delay=10,
            sleep=sleep,
        )

    return StackOutputs(
        vpc_id=vpc_id,
        subnet_a=subnets[0],
        subnet_b=subnets[1],
        alb_security_group_id=alb_group,
        alb_arn=alb_arn,
        alb_dns_name=alb_dns,
        weather_tg_arn=target_groups["weather"],
        advisor_tg_arn=target_groups["advisor"],
    )


def delete_stack(settings, aws, sleep=time.sleep) -> None:
    """Delete application resources; database teardown is a separate step."""
    print("Deleting Auto Scaling Groups and their instances...")
    for tier in settings.tiers:
        scaling.delete_asg(aws, tier.asg_name, sleep=sleep)
        if scaling.find_asg(aws, tier.asg_name):
            raise RuntimeError(
                "Auto Scaling Group is still deleting; rerun cleanup."
            )

    print("Deleting load balancer and target groups...")
    balancer.delete_load_balancer(aws, settings.alb_name)
    for tier in settings.tiers:
        retry_on(
            {"ResourceInUse"},
            lambda tier=tier: balancer.delete_target_group(
                aws, tier.tg_name
            ),
            attempts=12,
            delay=10,
            sleep=sleep,
        )

    vpc_id = network.default_vpc_id(aws)
    alb_group = network.find_security_group(
        aws, settings.alb_sg_name, vpc_id
    )
    network.restore_app_security_group(aws, settings, alb_group)
    if alb_group:
        network.delete_alb_security_group(
            aws, alb_group, sleep=sleep
        )

    print("Deleting launch templates and application IAM roles...")
    for tier in settings.tiers:
        compute.delete_launch_template(aws, tier)
        identity.delete_role_and_profile(aws, tier)

    print("Application infrastructure cleanup completed.")
