# Lab06: Weather Advisor — Results and Review

Course: ITMO 544 Cloud Computing
Region: us-east-2

## Architecture

The application used two Node.js services behind one Application Load
Balancer across two Availability Zones. Port 8080 routed requests to
Weather, while port 8081 routed requests to Advisor. Each tier ran in
its own Auto Scaling Group with desired capacity 1.

Weather called the ZIP lookup API and National Weather Service APIs.
It used a private PostgreSQL RDS instance for caching and lookup history.
Advisor called Weather through the ALB and requested clothing advice
from Gemini. Gemini failures produced explicitly labelled rule-based
fallback advice.

All AWS resource creation and deletion was performed through Python
and boto3. AWS CLI commands were used for inspection and verification.

## API Exploration

ZIP code 60616 resolved to Chicago, Illinois, at latitude 41.8426 and
longitude -87.6306. ZIP code 00000 returned HTTP 404.

NWS requires two calls after coordinates are known: the points endpoint
returns the forecast URL, and that URL returns forecast periods.
Including ZIP lookup, a cold weather request therefore involves three
upstream HTTP calls.

## Database Configuration

The deployed database had:

- Instance class: db.t4g.micro
- Storage: 20 GB
- Encrypted storage: true
- Multi-AZ: false
- Publicly accessible: false
- Backup retention: 0
- Status during testing: available

Snapshot and automated-backup checks found no retained copies.

The /history endpoint returned the stored Chicago lookup. A direct psql
query from the Weather instance confirmed the same row. The connection
used sslmode=verify-full and the Amazon RDS certificate bundle.

## Cache Results

The first measured request returned HTTP 200 with cache: miss in
0.522592 seconds. The repeated request returned HTTP 200 with cache: hit
in 0.065287 seconds.

The cached request was approximately eight times faster in this
measurement because it reused PostgreSQL data instead of calling the
upstream weather APIs. Network conditions and upstream response times
can vary, so this is an observed result rather than a guaranteed ratio.

A separate pytest integration test also confirmed a miss followed by a
hit for ZIP 60616 after the existing cache expired.

## Weather Logs

A weather_success entry for ZIP 60616 recorded cache: miss and ms: 371.
The subsequent cache: hit entry recorded ms: 2.

These values measure server processing time. Curl's total times also
include network overhead between my computer and the application.

## Secret Isolation

IAM simulation produced:

| Role | Database password | Gemini key |
| --- | --- | --- |
| Weather | allowed | implicitDeny |
| Advisor | implicitDeny | allowed |

The Advisor role could not read the database password. Weather could
not read the Gemini key. Each role's inline policy granted only
ssm:GetParameter on its own exact parameter ARN.

Secrets were stored as SSM SecureStrings rather than in .env, source
code, or launch-template user-data. This reduced accidental exposure
through Git, configuration files, and launch-template inspection.
Instances retrieved their own secret at runtime using their IAM roles.
The database password was generated using secrets.token_hex(16), and
the Gemini key was entered using getpass.

## Network Isolation

A direct request from my Mac to the Weather instance on port 3000
timed out, while the equivalent request through the ALB returned
HTTP 200. The application security group allowed the application port
from the ALB security group.

A connection attempt from my Mac to the database on port 5432 timed
out. The Weather instance successfully queried it through the allowed
security-group path.

## Database-Outage Experiment

The Python chaos test temporarily revoked database ingress. I restarted
Weather to close existing database connections before testing.

Results:

- First forecast request: HTTP 200, cache: unavailable, 3.335 seconds.
- Second forecast request: HTTP 200, cache: unavailable, 0.121 seconds.
- History endpoint: HTTP 503, database_unavailable.
- Weather logs recorded a db_error during the database read.
- The test restored the database rule in its finally block.
- A later request returned cache: hit, confirming recovery.

The 30-second database pause prevented every request from repeatedly
waiting for a failed database connection. Weather continued fetching
forecasts, while history correctly reported that stored data was
temporarily unavailable.

## Gemini Failure and Fallback

A temporary invalid key produced Gemini HTTP 400. Advisor still returned
HTTP 200 with useful clothing advice and source: fallback. The experiment
kept the original key in memory and restored it in a finally block.

Separate requests also demonstrated fallback during Gemini HTTP 503
responses and a timeout. A later smoke-test run passed all nine checks,
including source: gemini.

Silent fallback could hide a broken key or unavailable dependency because
requests would still appear successful. Explicit source labels and error
logs make this reduced behavior visible.

## Weather Instance Recovery

The recovery chaos test terminated Weather instance
i-077b1040a2c1dd67f using boto3.

Results:

- Weather outage first observed approximately 2.8 seconds after termination.
- Advisor /advice returned HTTP 502 with weather_service_unavailable.
- The observed Advisor failure response took 0.08 seconds.
- Advisor's home page continued returning HTTP 200.
- Auto Scaling launched replacement i-0f76d62e24bdf7e19.
- Weather returned a forecast again after 257.17 seconds.
- The 300-second recovery SLO passed.

After replacement, /history still included records created before the
outage, including the original row. Important state belongs in managed
storage rather than an EC2 instance's local disk so replacement does not
erase it.

## Backup Tradeoff

Disabling automated backups and deleting without a final snapshot avoided
retaining backup copies and associated storage after this temporary lab.
It also removed point-in-time recovery and left no recovery copy after
database deletion. I would not use this configuration for important
production, research, financial, or user data without an approved data
retention and recovery plan. A real system should use an appropriate
backup retention period, protected recovery copies, and tested restores.

## Data Sent to Gemini

The application prompt contained ZIP code, city, state, and public
forecast fields such as temperature, weather description, wind, and
precipitation chance. The test prompt contained only a sample weather
description. No database password, AWS credentials, lookup history, or
personal user profile was included in the prompt.

The Gemini API key was sent separately as an authentication header.
The free tier matters because quotas can affect availability and its
data-use terms must be reviewed before sending sensitive information.
I used public weather data and a personal account for this lab.

## Section 15.1 Review Answers

### 1. Where could secrets leak?

Secrets could leak through shell history, command-line arguments,
process environments, debug output, screenshots, Git commits, copied
configuration files, or an account with permission to decrypt SSM values.
Runtime access and administrative access still need protection even when
secrets are stored in SSM.

### 2. What if both roles had ssm:* on *?

Both tiers could potentially read, change, or delete unrelated parameters
allowed by that broad policy. Compromise of one tier would expose a much
larger set of secrets and configuration. Exact actions and resource ARNs
limit that exposure.

### 3. Why expose the ALB rather than instances and the database?

The ALB is the public application entry point and routes requests to
healthy instances. Restricting application-port access to the ALB reduces
direct access paths. The database is an internal dependency and does not
need public access.

### 4. What weakness comes from the shared application security group?

Both Weather and Advisor share the security group permitted by the
database rule. Therefore, Advisor has a network path to PostgreSQL even
though its IAM role cannot read the database password. Separate security
groups for the two tiers would allow database ingress only from Weather.

### 5. What database login should replace the master user?

Use a dedicated runtime login restricted to the lookups table, with
SELECT and INSERT privileges, schema USAGE, and the sequence privileges
needed by the generated ID. A separate provisioning identity should
create the schema, table, and index. The application would then no longer
need to run CREATE TABLE or CREATE INDEX at startup.

### 6. Why verify the TLS certificate?

Certificate verification checks the database server's identity against a
trusted CA and hostname. Encryption without identity verification can
still allow a connection to an impersonated server.

### 7. How do placeholders prevent SQL injection?

The app uses:

    INSERT INTO lookups (zip, payload) VALUES ($1, $2)

Values are supplied separately from the SQL statement. Joining untrusted
input directly into SQL could allow input to change the query's meaning.

### 8. What would be lost without backups?

Accidental deletion, corruption, or a destructive operation could cause
permanent data loss. Production settings should reflect recovery-point
and recovery-time requirements, with backups and tested restoration.

### 9. Who decides when data may be destroyed?

The organization's data owner and authorized governance, security, legal,
and compliance functions establish retention and destruction rules.
Infrastructure operators should follow those approved rules rather than
delete important data solely to reduce costs.

### 10. What could raw model output do to a web page?

Unescaped model output could introduce HTML or script content and create
cross-site scripting. Depending on browser protections, this could modify
the page, trick users, or perform actions in the page's origin.
escapeHtml makes the text display as text rather than markup.

### 11. What changes are needed for real users or a school-owned account?

Review account authorization, provider terms, data classification,
consent, retention, and institutional requirements. Minimize identifying
data and establish an approved provider arrangement before sending
restricted information. Paying for an API alone does not establish that
the data sharing is appropriate.

### 12. How could someone consume the public service's quota?

An attacker could repeatedly request advice through the public endpoint.
Application rate limits, authentication, WAF rules, monitoring, and
appropriate usage controls would reduce abuse and unexpected usage.

### 13. Why label fallback and unavailable cache states?

source: fallback tells the caller that advice came from rules rather
than Gemini. cache: unavailable distinguishes a database failure from a
normal cache miss. These labels support honest user communication and
operational diagnosis.

## Graduate Discussion: What Moto Could Not Prove

Moto tested the Python infrastructure code without contacting real AWS.
It verified request behavior and mocked resource settings, including RDS
configuration, security-group rules, secret policy documents, repeated
creation, and cleanup. It also let the tests check launch-template
contents and ALB-first ordering. These checks did not demonstrate that
the deployed services actually worked over a real network.

Real network isolation required integration tests. The target-health
tests checked actual ALB targets. The application-port tests combined
successful ALB requests with failed direct connections. The database
network test confirmed that its endpoint resolved but port 5432 was not
reachable from my computer. The application integration tests verified
forecasts, advice, history, validation errors, and cache behavior.

Moto could not prove the actual TLS handshake or certificate validation
performed by PostgreSQL clients. I checked this separately through a
successful psql query using sslmode=verify-full and the RDS CA bundle.
That was manual deployed-stack evidence, not an automated TLS test.
The real application also used certificate verification.

Mocked backup and deletion results could not prove that AWS retained no
billable recovery copies. Real RDS inspections before and after cleanup
checked database instances, snapshots, and automated backups. Likewise,
IAM simulation against the deployed roles supplemented unit tests of
their policy documents; the simulation evaluated the requested action
and resource but was not itself a secret-read request.

Finally, moto did not prove real dependency failures or replacement
timings. The database chaos test demonstrated continued forecast service
during a network outage. The instance-termination test measured real
Auto Scaling recovery and Advisor behavior. Gemini experiments showed
real invalid-key, service-error, and timeout fallback behavior.

## Graduate Discussion: Python Compared with Bash

Python made the database lifecycle easier to structure into reusable
functions for configuration, security groups, subnet groups, passwords,
creation, waiting, and deletion. Boto3 returned structured dictionaries,
which avoided parsing CLI text, while specific ClientError handling
distinguished missing resources from unexpected failures. Moto and
pytest made repeatable creation, policy structure, RDS settings, and
cleanup easier to test without provisioning real resources. Bash was
shorter for individual inspection commands, but maintaining structured
state, handling errors, and testing complete workflows was clearer in
Python.

## Test Results

Tests passed in separate runs:

- 16 unit tests without real AWS credentials.
- 9 application smoke checks.
- 9 pytest smoke integration cases.
- 1 additional cache miss-then-hit integration test.
- 11 infrastructure integration tests.
- 1 database-outage chaos test.
- 1 Weather instance-recovery chaos test.

The completed suite contains 39 pytest cases:
16 unit, 21 integration, and 2 chaos.

## Cleanup Verification

Python cleanup removed the application stack and database. The Gemini
parameter was then deleted through Python.

Verification returned empty lists for:

- Database instances, snapshots, and automated backups.
- Database subnet groups.
- Application load balancers and target groups.
- Auto Scaling Groups and launch templates.
- Application IAM roles and instance profiles.
- Lab ALB and database security groups.
- Active or stopped application instances.
- Both secret parameters.

The verifier reported:
PASS: all checked Lab06 resources are absent.

The pre-existing application security group, SSH key-pair record, and
deployment-user permissions were outside these Lab06 resource checks.
