# Lab 3: Multi-AZ Resilience with ALB and Auto Scaling

## Architecture

The Application Load Balancer spans us-east-2a and us-east-2b.
Listener 8080 forwards to the Uploader, and listener 8081 forwards
to the Viewer. Both applications listen on port 3000.

Each application has an Auto Scaling Group with minimum, maximum,
and desired capacity set to 1. Both groups can launch instances
into either configured Availability Zone. Application port 3000
accepts traffic only from the ALB security group. Separate IAM
roles provide the required S3 permissions.

Each tier has one instance, so this design provides automatic
replacement rather than a healthy standby.

## Recovery Results

| Tier | SLO (seconds) | Recovery (seconds) | Result |
|---|---:|---:|---|
| Uploader | 180 | 71.5 | PASS |
| Viewer | 180 | 86.0 | PASS |
| Uploader | 60 | 87.3 | FAIL |

Recovery time is measured from the termination request to the first
successful probe in a sequence of three consecutive HTTP 200 responses.

## Recovery-Time Analysis

The Uploader recovered in 71.5 seconds and the Viewer recovered in
86.0 seconds, both meeting the 180-second SLO. When I tightened the
Uploader SLO to 60 seconds, recovery took 87.3 seconds, so the run
failed the target even though service was restored. Initial failures
were detected within 2.4–4.5 seconds. Most of the remaining recovery
window likely involved replacement-instance startup, dependency
installation, application startup, and ALB health checks. The HTTP
logs do not separate these stages, so their individual contributions
cannot be determined precisely. A prebuilt AMI could reduce startup
time, while a second healthy instance could help maintain service
during replacement.

## Changes to the Scripts

I retained the previous S3, IAM, instance-profile, and launch-template
logic and replaced direct EC2 run-instances calls with two Auto Scaling
Groups. Each group maintains one instance from its launch template
and can launch into either configured AZ. I added the ALB, two
listeners, two target groups, ELB health checks, and application
security-group rules allowing traffic from the ALB. The cleanup
script removes the ASGs and instances, ALB, target groups, and ALB
security group before removing launch templates, IAM resources,
and the S3 bucket. This provides automatic replacement while
preserving stable application URLs.

## Submission Evidence

- screenshots/: deployment, application, target-health, recovery,
  and cleanup screenshots.
- testing/: Python source, requirements, CSV poll logs, and console output.
- uploader-app/: Uploader application source.
- viewer-app/: Viewer application source.
- create_app_stack.sh: deployment script.
- delete_app_stack.sh: cleanup script.

## Cleanup

Cleanup completed successfully. A subsequent curl request returned
"Could not resolve host" for the deleted ALB hostname.

## Configuration

AWS credentials and local .env configuration are excluded.
Deployment requires an appropriate AMI, key pair, app security group,
and resource settings.