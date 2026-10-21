"""Find and terminate the instance managed by an ASG."""
import boto3


def get_inservice_instance_id(asg_name, region):
    client = boto3.client("autoscaling", region_name=region)
    response = client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )
    groups = response.get("AutoScalingGroups", [])
    if not groups:
        raise RuntimeError(f"ASG not found: {asg_name}")

    instances = [
        instance["InstanceId"]
        for instance in groups[0].get("Instances", [])
        if instance["LifecycleState"] == "InService"
    ]
    if len(instances) != 1:
        raise RuntimeError(
            f"Expected one InService instance; found {len(instances)}."
        )
    return instances[0]


def terminate_instance_via_asg(instance_id, region):
    client = boto3.client("autoscaling", region_name=region)
    client.terminate_instance_in_auto_scaling_group(
        InstanceId=instance_id,
        ShouldDecrementDesiredCapacity=False,
    )
