# Lab06: Weather Advisor in the Cloud

ITMO 544 Cloud Computing — Python/boto3 implementation.

## Overview

Two Node.js services run behind an Application Load Balancer:

- Weather on port 8080 provides forecasts, PostgreSQL caching, and history.
- Advisor on port 8081 provides Gemini clothing advice with labelled fallback.

Each service has its own Auto Scaling Group and IAM role.
Weather can read only the database password parameter.
Advisor can read only the Gemini API key parameter.
Both secrets are stored as SSM SecureStrings.

The private PostgreSQL RDS instance uses db.t4g.micro, 20 GB encrypted
storage, single-AZ deployment, and backup retention 0.

## Project Files

- stack/: Python infrastructure package.
- weather-app/: Weather application.
- advisor-app/: Advisor application.
- tests/: unit, integration, and chaos tests.
- testing/: smoke test and Gemini fallback experiment.
- screenshots/: assignment evidence.
- WRITEUP.md: results, review answers, and graduate discussion.
- previous/: archived Lab05 reference files.
- .env.example: non-secret configuration template.

Lab05 remains unchanged in the separate ../lab05 folder.

## Rebuilding the Lab

The following are reference instructions for a future deployment.
The completed lab has already been cleaned up.

Run from the lab06 folder with its virtual environment active.
Install dependencies from requirements.txt.
Copy .env.example to .env and supply your non-secret settings.
Configure AWS credentials separately.

Run store_gemini_key.py and enter the key at its hidden prompt.
Run create_database.py, then create_weather_stack.py.
Wait for both target groups to become healthy before testing.

The database password is generated automatically and stored in SSM.
The ALB is created before the application launch templates.
Secret values are never embedded in user-data.

Check Gemini model availability and free-tier eligibility before rebuilding.

## Testing

Unit tests:
python -m pytest -m "not integration and not chaos" -v

Smoke test:
python testing/app_smoke_test.py

Integration tests against a deployed stack:
python -m pytest -m integration -v

Database outage test:
python -m pytest tests/test_database_chaos.py -m chaos -s -v

When prompted, restart Weather in the Weather EC2 browser terminal.
Return to the local test terminal and press Enter.
The test restores the database access rule in a finally block.

Weather instance recovery test:
python -m pytest tests/test_recovery_chaos.py -m chaos -s -v

This test terminates Weather and measures Auto Scaling replacement.

Secret isolation:
python verify_secret_permissions.py

Invalid-key experiment:
python testing/experiment_gemini_fallback.py

The experiment restores the original key afterward.

The cache miss-then-hit integration test may wait for cache expiry.
Avoid other weather requests while it runs.

## Recorded Results

Tests passed in separate runs:

- 16 unit tests without real AWS credentials.
- 9/9 smoke checks.
- 21 pytest integration cases.
- 2 chaos tests.

The pytest suite contains 39 cases:
16 unit, 21 integration, and 2 chaos.

Measured cache miss: 0.522592 seconds.
Measured cache hit: 0.065287 seconds.

During database failure, forecasts continued returning HTTP 200 with
cache: unavailable. History returned HTTP 503.

During the invalid-key experiment, Advisor returned HTTP 200 with
source: fallback. Gemini rejection was recorded in its journal.

Weather recovered after instance termination in 257.17 seconds,
within the 300-second SLO.

Advisor returned HTTP 502 during the Weather outage in 0.08 seconds.
Its home page remained available with HTTP 200.

PostgreSQL lookup history survived Weather instance replacement.

## Screenshots

The 30 Lab06 screenshots are:

01_nws_forecast.png
02_gemini_ssm_success.png
03_database_created.png
04_database_settings.png
05_no_snapshots_or_backups.png
06_weather_stack_created.png
07_weather_target_healthy.png
08_cache_miss_hit_timings.png
09_lookup_history.png
10_advisor_json.png
11_advisor_page.png
12_weather_journal.png
13_postgresql_query.png
14_iam_secret_isolation.png
15_direct_vs_alb_access.png
16_database_private_access.png
17_smoke_test.png
18_gemini_503_fallback.png
19_pytest_app_integration.png
20_pytest_infrastructure_integration.png
21_database_outage_test.png
22_database_outage_logs.png
23_invalid_key_fallback.png
24_invalid_key_logs.png
25_weather_recovery_chaos.png
26_history_after_recovery.png
27_unit_tests_no_credentials.png
28_pytest_cache_miss_hit.png
29_lab06_folder_layout.png
30_cleanup_verification.png

Earlier-lab screenshots copied into this directory are reference material,
not Lab06 evidence.

## Cleanup

Cleanup was performed in this order:

1. python delete_weather_stack.py
2. python delete_database.py
3. python verify_cleanup.py

Auto Scaling deletion exceeded the initial wait period.
Rerunning cleanup after group deletion completed successfully.

Database deletion skipped the final snapshot and deleted automated backups.
The verifier deleted the Gemini parameter before checking remaining resources.

Verification returned empty lists for:

- Database instances, snapshots, and automated backups.
- Database subnet groups.
- Load balancers and target groups.
- Auto Scaling Groups and launch templates.
- Application IAM roles and instance profiles.
- Lab ALB and database security groups.
- Active or stopped application instances.
- Both secret parameters.

The verifier reported:
PASS: all checked Lab06 resources are absent.

The application URLs are no longer available.

The pre-existing application security group, SSH key-pair record, and
deployment-user permissions are outside these Lab06 cleanup checks.

## Written Submission

See WRITEUP.md for experiment explanations, Section 15.1 review answers,
and the graduate discussions required by Section 16.2.

## Secret Handling

Do not commit .env, AWS credentials, private SSH keys, or secret values.
Generated deployment output files and virtual environments are excluded
from Git.