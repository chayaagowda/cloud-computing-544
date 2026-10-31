"""Read Weather instance boot logs without SSH."""

import base64
import boto3

ec2 = boto3.client("ec2", region_name="us-east-2")

reservations = ec2.describe_instances(Filters=[
    {"Name": "tag:Name", "Values": ["weather-app"]},
    {"Name": "instance-state-name", "Values": ["running"]},
])["Reservations"]

instances = [
    instance
    for reservation in reservations
    for instance in reservation["Instances"]
]

if not instances:
    raise SystemExit("No running Weather instance found.")

for instance in instances:
    instance_id = instance["InstanceId"]
    print("\nInstance:", instance_id)

    result = ec2.get_console_output(
        InstanceId=instance_id,
        Latest=True,
    )
    output = result.get("Output", "")
    if not output:
        print("Console output is not available yet.")
        continue

    try:
        output = base64.b64decode(
            output, validate=True
        ).decode("utf-8", errors="replace")
    except (ValueError, base64.binascii.Error):
        pass

    print("\n".join(output.splitlines()[-100:]))