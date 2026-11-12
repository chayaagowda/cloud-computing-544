# Lab 07 — Weather Advisor in Terraform

Use this folder with the Lab 07 handout. It contains the Part A exercise,
the two reference apps, the main Terraform configuration, and the nine-check
smoke test. Record the output from your own AWS account as you work.

## Part 0 — Set up in the course repository

Confirm your Lab 06 stack was cleaned up. If it is still running, use your
Lab 06 teardown commands, starting with the app stack. The Lab 07 weather
service uses DynamoDB, even if your Lab 06 implementation used RDS.

Extract the ZIP and put the extracted `lab07` folder next to `lab06` in
`~/cloud-computing-544`. If you already have a `lab07` folder, review its files
before merging the starter. Keep the original Lab 06 scripts in
`lab07/previous/` for Part F. From the repository root, in macOS Terminal:

```sh
mkdir -p lab07/previous
find lab06 -maxdepth 1 -type f \( -name 'create_*.sh' -o -name 'delete_*.sh' -o -name 'create_*.py' -o -name 'delete_*.py' \) -exec cp -n {} lab07/previous/ \;
cd lab07
terraform version
aws sts get-caller-identity --query '{UserId:UserId}' --output json
export AWS_DEFAULT_REGION=us-east-2
```

The identity command suppresses the account number and ARN for the submission
screenshot. Use Terraform 1.11 or newer for this starter. Your main stack
uses the AWS 5.100.x provider and a write-only parameter value.

## Part A — Practice with DynamoDB

1. From `lab07/first-taste`, run `terraform init`, then `terraform plan`.
   Save the plan showing one addition. Apply it.
2. Confirm the table status with
   `aws dynamodb describe-table --table-name itmo-444-544-terraform-demo --query Table.TableStatus --output text`.
   Run `terraform apply` a second time and capture the no-change result.
3. Add `tags = { Purpose = "first-taste" }` to the table resource. Capture the
   in-place update plan and apply it.
4. Change the table's `hash_key` and its attribute name from `id` to `name`.
   Capture the replacement plan. Revert both edits before applying anything.
   Replacement would delete the old table and its items.
5. Run `terraform destroy` from `first-taste`. Return to `lab07` for Part E.

## Parts B–E — Configure and deploy

Copy `terraform.tfvars.example` to `terraform.tfvars`. Enter the existing
EC2 key-pair name in Ohio, your contact email, and your current public IPv4
address with `/32`. The matching private key can stay in its existing local
location. Check the public IPv4 value from your Mac and use your real address
in place of the example address. You will need SSH for the Part F evidence.

Use the Part D prompt and test with your own Google AI Studio key. Then
run the following from `lab07` and capture the validation and plan summary.

```sh
terraform init
terraform fmt -recursive -check -diff
terraform validate
terraform plan -out=tfplan
```

Record the start time, apply the reviewed plan with `terraform apply tfplan`,
and upload the real key using Part E3. Once both targets are healthy and the
key cache has had time to expire, run `python3 testing/app_smoke_test.py` and
open the URL printed by `terraform output -raw advisor_url`. Submit ZIP `60616` and capture the result page.

## Part F — Capture the live evidence

Follow the handout's experiments in order. For the line-count table, count the
actual files in `previous/`. If your Lab 06 used Python, identify those numbers
as Python and include the shell counts separately if shell scripts are present.
Use `[[:space:]]` in place of `\s` for portable grep expressions on macOS.

For Step 4, wait for the weather instance refresh to finish before capturing
the new `CACHE_SECONDS=60` setting. After restoring 600, wait for the next
refresh to finish before running the drift tests. Restore the weather health
check and advisor listener before testing IAM and fallback behavior.

### Step 7 adjustment for this starter

The handout uses the ordinary `value` argument with `ignore_changes`. The
AWS provider can read the decrypted value during refresh, so ignoring the diff
alone does not guarantee secret-free state or plans. This starter creates the
placeholder using `value_wo` and `value_wo_version`. Terraform can manage the
parameter without persisting the refreshed value. Keep the write-only version
at 1 during the lab. Incrementing it requests another write of the placeholder.

After storing the real key, capture the no-change plan and the zero count
below. The placeholder grep in the handout will be empty because the
placeholder is write-only too. Explain the change in your write-up.

```sh
terraform plan -no-color | tail -5
grep -c 'AIza' terraform.tfstate
```

For Step 8, wait longer than the configured key cache after inserting the
intentionally invalid key. Capture the fallback responses and the
`gemini_error` journal line, then restore the real key.

For Steps 9 and 10, complete the Lambda table and review questions, record
the destroy plan and elapsed times, and run the handout's cleanup checks.
Look for Lab 07 resource names and its project tag when unrelated resources
also appear in account-wide commands. Compute estimated spend from the
elapsed stack runtime at the handout's hourly estimate.

## Submission

Use the ten items in Section 14 as the final checklist. Include the lock files
generated by `terraform init` if submitting through Git. Keep state, saved
plans, `terraform.tfvars`, and private keys out of the submission.
