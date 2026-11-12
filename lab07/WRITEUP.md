# Lab 07 — Infrastructure as Code with Terraform

## Overview

I rebuilt the Weather Advisor stack in AWS Ohio (`us-east-2`) using Terraform v1.17.1 and AWS provider v5.100.0. An Application Load Balancer exposed the weather tier on port 8080 and the advisor tier on port 8081. Each tier ran on EC2 in its own Auto Scaling group. The weather tier stored forecasts and recent lookups in DynamoDB. The advisor called the weather tier for forecast data and read its Gemini API key from an SSM Parameter Store `SecureString`.

## Part A — Terraform Practice with DynamoDB

My first plan proposed creating one DynamoDB table, `itmo-444-544-terraform-demo`. After applying the plan, I verified that the table was `ACTIVE`. Running `terraform apply` a second time produced `No changes` and reported zero resources added, changed, or destroyed.

I added the `Purpose = "first-taste"` tag. Terraform showed an in-place update (`~`) with `0 to add, 1 to change, 0 to destroy`. I then changed the partition key and its attribute name from `id` to `name`. That plan showed a replacement (`-/+`) with `1 to add, 0 to change, 1 to destroy`. A replacement matters because deleting a DynamoDB table also removes the items it contains. I reverted the key change after capturing the plan and destroyed the practice table.

Evidence includes screenshots 01–05.

## Parts B–E — Main Stack Deployment and Validation

I initialized Terraform, formatted the configuration, and verified that `terraform validate` succeeded. The main plan proposed creating 25 resources. The first deployment attempt created 19 resources and stopped when AWS denied `ssm:PutParameter` for the Gemini parameter. I removed that partial stack and attached the `Lab07GeminiParameterAccess` policy to my deployment user. The policy scoped parameter writes, reads, deletion, and tagging to the Lab 07 Gemini parameter. It limited `ssm:DescribeParameters` to `us-east-2`.

After attaching the policy, I generated a fresh plan. Terraform successfully added all 25 resources. I stored the working Gemini key in Parameter Store using the AWS CLI and a hidden terminal prompt.

The smoke test passed all nine checks. The weather endpoint returned a forecast, a repeat lookup hit the DynamoDB cache, and the history endpoint returned the saved lookup. The weather tier returned HTTP 400 for an invalid ZIP and HTTP 404 for an unknown ZIP. The advisor returned clothing advice with `source=gemini`, rejected an invalid ZIP with HTTP 400, and served its home page successfully. I also tested ZIP code `60616` in the browser.

Evidence includes screenshots 06–11, along with the saved AccessDenied and partial-cleanup evidence.

## Part F — Terraform Experiments

### Step 1 — Compare the Code and Operational Effort

| Item | Lab 06 Python | Lab 07 Terraform |
|---|---:|---:|
| Top-level creation scripts or configuration | 141 lines | 428 lines |
| Separate teardown scripts | 70 lines | 0 lines |

The Lab 06 counts cover the two copied creation scripts and two copied deletion scripts. They exclude the Python modules those scripts import. The Lab 07 count covers the top-level `.tf` files and `user-data.sh.tftpl`. These counts describe the files measured, so they do not establish which implementation is shorter overall.

The Python entry points call the creation and deletion steps in an explicit order. In Terraform, references describe most dependencies, and `terraform destroy` uses state to determine which resources to remove. Terraform reduced the amount of custom teardown and dependency-ordering logic I had to maintain.

### Step 2 — Inspect the Dependency Graph

The graph included this edge:

    "aws_launch_template.tier" -> "aws_lb.main";

The advisor launch template uses the load balancer's DNS name in `WEATHER_URL`. This reference gives Terraform a direct dependency on the load balancer and gives the advisor the address it needs to call the weather service. Removing the reference would remove that direct dependency. The advisor would still need a valid weather endpoint from another source.

Evidence includes screenshot 12.

### Step 3 — Verify a No-Change Plan

After deployment and after storing the Gemini key separately, `terraform plan` reported `No changes. Your infrastructure matches the configuration.` Terraform refreshed its view of AWS and found no changes to make. An imperative create script depends on the checks its author implemented to decide which steps it can safely run again.

Evidence includes screenshot 13.

### Step 4 — Update the Cache and Refresh the Weather Instance

I changed the weather cache from 600 seconds to 60 seconds. Terraform planned two in-place updates, one to the weather launch template and one to the weather Auto Scaling group. The launch template received a new version, and the Auto Scaling group replaced the weather instance through an instance refresh.

AWS reported that the refresh was `Successful` at 100%. On the replacement instance, `systemctl cat weather` showed `Environment="CACHE_SECONDS=60"`. I then restored the 600-second setting, applied the two weather-tier updates, and confirmed that the second refresh succeeded.

Evidence includes screenshots 14–16.

### Step 5 — Introduce and Correct Drift

I changed the weather target group's health-check path directly in AWS from `/` to `/nope`. Terraform detected the difference and planned one in-place update. Applying the plan restored the path to `/`.

I also deleted the advisor listener on port 8081 directly in AWS. Terraform planned to create one resource, `aws_lb_listener.tier["advisor"]`. Applying the plan recreated the listener and reported `1 added, 0 changed, 0 destroyed`. These tests showed that Terraform can detect both a modified setting and a missing resource. A create script that checks only whether a target group already exists can miss a changed health-check path.

Evidence includes screenshots 17–18.

### Step 6 — Verify Least Privilege

| EC2 role | Action | IAM simulator result |
|---|---|---|
| Weather | `dynamodb:PutItem` on the weather table | `allowed` |
| Weather | `ssm:GetParameter` on the Gemini parameter | `implicitDeny` |
| Advisor | `ssm:GetParameter` on the Gemini parameter | `allowed` |
| Advisor | `dynamodb:GetItem` on the weather table | `implicitDeny` |

The weather role could write to DynamoDB, and the advisor role could retrieve the Gemini key. If someone gained access to the advisor role's credentials, those credentials would allow retrieval of the Gemini parameter. The simulated `dynamodb:GetItem` request against the weather table was implicitly denied.

Evidence includes screenshot 19.

### Step 7 — Check Terraform State for the Gemini Key

This configuration uses the AWS provider's write-only `value_wo` argument with `value_wo_version = 1`. Terraform created the parameter with a placeholder, and I used the AWS CLI to store the actual key. After the update, `terraform plan` reported `No changes`, and `grep -c 'AIza' terraform.tfstate` printed `0`.

A write-only argument keeps its value out of Terraform state and saved plans. With an ordinary `value` argument, `ignore_changes` can suppress an update to the parameter while the provider can still read the live value during refresh. The zero count was a check for the expected key prefix in my local state.

Evidence includes screenshot 20.

### Step 8 — Test Gemini Failure and Recovery

I temporarily replaced the key in Parameter Store with a dummy value. The advisor still returned clothing advice and labeled the response `source = fallback`. The advisor service log recorded `gemini_error` and an HTTP 400 response from Gemini. A user still receives advice during an upstream failure, but repeated failures can go unnoticed if operators rely only on a successful response from the advisor. The response's `source` field and the service logs expose that difference.

I restored the working key, which AWS saved as parameter version 4. After the advisor's key cache expired, a new request returned `source = gemini`.

Evidence includes screenshots 21–23.

### Step 9 — Moving the Advisor to Lambda

| Advisor on EC2 | Advisor on Lambda | Where the work goes |
|---|---|---|
| Launch template and user data | Lambda function, runtime, and deployment package | Terraform defines the function configuration. The developer packages the handler and Lambda manages the runtime. |
| Auto Scaling group and instance refresh | Lambda invocation scaling and versioned releases | Lambda manages capacity. The developer can publish a version and update an alias for rollout. |
| App security group rule for port 3000 | ALB invokes the function through a Lambda target group | Terraform defines permission scoped to the advisor target group. The EC2 weather tier still needs its app security group rule. |
| EC2 instance profile and IAM role | Lambda execution role | Terraform grants the execution role access to the Gemini parameter and permission to write logs. |
| Node.js installation and `systemd` service | Managed Node.js runtime and function handler | The developer adapts the advisor to read ALB events and return the expected response format. |
| Target health checks and EC2 grace period | Optional Lambda target health checks and a function timeout | The developer configures timeout behavior and tests downstream failures. |

The ALB, port 8081 listener, weather tier, DynamoDB table, and Gemini SSM parameter would remain in the design. The advisor listener would forward to a Lambda target group. Terraform would define the function, execution role, target group, target attachment, and permission for the ALB to invoke the function.

Lambda would handle server provisioning and scaling. Terraform would still define and manage the AWS resources and their dependencies. I would test cold-start latency, timeouts, the ALB request and response format, and fallback when the weather service or Gemini is unavailable. This was a written design exercise.

AWS reference: [Use Lambda functions as targets of an Application Load Balancer](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/lambda-functions.html).

### Step 10 — Cleanup and Cost

The final destroy plan showed `0 to add, 0 to change, 25 to destroy`. Applying it reported `0 added, 0 changed, 25 destroyed`. Terraform's state list was empty after cleanup. I also checked AWS for resources with the Lab 07 project tag. The first tag query still listed six security-group rules, so I checked the rules directly in EC2 and repeated the tag check for the final cleanup evidence.

- Deployment started: Nov 11, 2026, at 7:48:06 PM CST
- Cleanup finished: Nov 11, 2026, at 9:27:17 PM CST
- Elapsed time: 1 hour, 39 minutes, 11 seconds
- Estimated cost for the successful deployment: 1.653 hours × $0.07/hour ≈ $0.12

This is an estimate using the lab's hourly rate. It covers the successful deployment and excludes the earlier partial deployment.

Evidence includes screenshots 24–26.

## Review Questions

1. **Declarative and imperative infrastructure.** An imperative script makes it straightforward to run a one-time action, such as prompting for a secret and storing it without writing it into configuration. Terraform compares desired configuration with live AWS resources and can plan a correction when it detects drift.

2. **State.** Losing the state file can leave resources running while Terraform loses the mapping between configuration addresses and AWS resource IDs. If two people apply from separate state copies, they can make conflicting changes or attempt duplicate creation. A shared remote backend with locking lets a team coordinate updates to one authoritative state.

3. **Secrets in state and plans.** A secret can enter state through a normal resource argument, a data source, or a rendered template. A saved plan can include configuration values and a copy of state, so it should be protected too. Marking a value `sensitive` hides it from normal CLI output. The write-only parameter argument prevents its value from being persisted.

4. **Reviewing a plan.** A plan shows which resources Terraform intends to add, update, replace, or destroy. I pay particular attention to `-/+` because replacing a stateful resource such as a DynamoDB table can delete stored data.

5. **Version constraints and the lock file.** An unconstrained provider upgrade can introduce breaking changes or different defaults. The version constraint sets the supported range. Committing `.terraform.lock.hcl` records the selected provider version and checksums for reproducible installation.

6. **`for_each` and `count`.** `for_each` identifies the tiers by the keys `weather` and `advisor`. Removing a tier leaves the other tier's key intact. With a `count` based on a list, removing an earlier entry can shift the indexes of later resources. `count` fits the optional SSH rule because the rule has either zero or one instance.

7. **Resource ownership.** If a person and Terraform both edit the same resource, the live settings can drift from the configuration. A later apply can reverse the manual change. In this lab, Terraform owned the new application security group, which gave it a clear record of the rules it managed.

8. **Detecting drift.** A team can run scheduled Terraform plans in CI, use `-detailed-exitcode` to detect a proposed change, and review the resulting plan. Manual production changes should be limited to authorized operators and reconciled with the configuration afterward.

9. **Blast radius.** The scope of a change depends on which resources consume the changed setting and how those resources react to it. `cache_seconds` reached the weather launch template and weather Auto Scaling group. Separating independent tiers into configurations with separate state can reduce how many resources a single apply can affect.

10. **Least privilege and references.** Using a reference to the actual table or parameter ARN keeps the policy tied to the resource Terraform creates. That reduces the risk of typing the wrong resource name, account, or region.

11. **Terraform and Lambda.** Both let developers provide a definition while a tool or service handles underlying work. Terraform manages infrastructure when it runs. Lambda executes application code in response to events. Terraform reduced custom deployment and teardown work, while permission troubleshooting, secret management, and application validation still required separate checks.

12. **Protecting resources from destroy.** In production, I would protect stateful resources such as a DynamoDB table with `prevent_destroy` where appropriate, keep backups, and restrict deletion permissions. I would review destroy and replacement plans before applying them.