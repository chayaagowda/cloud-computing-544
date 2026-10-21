# Lab 05: Infrastructure as Python with boto3 and pytest

## Overview

This lab replaces the previous Bash/AWS CLI deployment with an
importable Python package using boto3. The previous lab remains in
../lab03; this assignment is in lab05.

## Architecture

An Application Load Balancer spans us-east-2a and us-east-2b.
Listener 8080 forwards to the Uploader and listener 8081 to the Viewer.
Both apps listen on port 3000, accessible only from the ALB.

Each tier has an Auto Scaling Group with minimum, maximum, and desired
capacity of 1. Separate IAM roles grant the Uploader permission to write
shared.txt and the Viewer permission to read shared.txt and list the
bucket. The bucket blocks public access.

## Commands

Run these commands individually from lab05 with the virtual environment
active. Deployment and integration/chaos tests use real AWS resources.

- Install dependencies: python -m pip install -r requirements.txt
- Local unit tests: python -m pytest -v
- Create stack: python create_stack.py
- Integration tests: python -m pytest -m integration -v
- Recovery tests: python -m pytest -m chaos -s -v
- Cleanup: python delete_stack.py
- Repeat cleanup: python delete_stack.py

.env and generated stack output files are excluded from Git.

## Results

- 19 unit tests passed.
- 14 integration tests passed.
- Changing the Uploader permission to s3:* caused its policy test to
  fail. Restoring s3:PutObject returned all 19 tests to passing.
- Both recovery tests passed with a 180-second SLO.
- Both recovery tests failed with a 60-second SLO.
- The SLO was restored to 180 seconds afterward.

| Tier | Recovery with 180s SLO | Recovery with 60s SLO |
| --- | --- | --- |
| Uploader | 75.39 seconds: passed | 76.68 seconds: failed |
| Viewer | 69.50 seconds: passed | 81.76 seconds: failed |

## What moto Proved and Its Limitations

The moto tests exercised the actual create and delete functions against
simulated AWS services. They checked resource configuration, repeated
creation without duplicate stack resources, repeated cleanup, instance
profile membership, and application security-group rules. They could
not prove that real instances boot, npm installation succeeds, apps
serve HTTP, IAM permissions behave as intended, or recovery meets the
SLO. Real integration tests checked infrastructure and application
behavior, including IAM policy simulation and upload/read operations.
Chaos tests measured recovery after terminating an instance per tier.

## Targeted Error Handling Compared with Bash

ignore_errors("EntityAlreadyExists") suppresses only that expected AWS
error. Unexpected failures, including access denials, still stop the
script. In contrast, 2>/dev/null || true hides error output and accepts
every failure, potentially masking permission problems or incorrect
commands. Targeted handling supports repeated execution while keeping
unexpected failures visible.

## Challenge 5

The Bash expression 2>/dev/null || true was shorter than the Python
context manager and explicit error-code checks. The extra Python lines
ensure that only expected failures are ignored and unexpected errors
remain visible for diagnosis.

## Output Comparison

Python outputs were preserved in screenshots/python_stack_outputs.txt.
The previous Bash stack_outputs.env was unavailable, so historical key
and subnet equality could not be verified directly. The Python stack
used subnet-07877a7205c1c0f15 and subnet-06669c5876c315763.

## Cleanup

Initial attempts encountered a DNS resolution failure and a temporary
ResourceInUse error. Retrying completed cleanup successfully, and a
further cleanup run completed without errors. AWS queries returned
empty lists for load balancers, Auto Scaling Groups, and matching lab
IAM roles. The prerequisite app security group, key pair, and default
VPC remain.

## Required Screenshots

1. 01_lab05_folder_layout.png
2. 02_unit_tests_19_passed.png
3. 03_intentional_policy_failure.png
4. 04_python_stack_created.png
5. 05_integration_tests_passed.png
6. 06_chaos_recovery_results.png
7. 07_tightened_slo_failure.png
8. 08_cleanup_complete.png
9. 09_cleanup_second_run.png
