#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

set -a
source .env
set +a
export AWS_PAGER=""


# BEGIN ALB-ASG CLEANUP
echo "== Deleting Auto Scaling Groups and their instances =="

for asg in "$UPLOADER_ASG_NAME" "$VIEWER_ASG_NAME"; do
  exists=$(aws autoscaling describe-auto-scaling-groups \
    --auto-scaling-group-names "$asg" \
    --query 'length(AutoScalingGroups)' \
    --output text --region "$AWS_REGION")

  if [[ "$exists" != "0" ]]; then
    instance_ids=$(aws autoscaling describe-auto-scaling-groups \
      --auto-scaling-group-names "$asg" \
      --query 'AutoScalingGroups[].Instances[].InstanceId' \
      --output text --region "$AWS_REGION")

    aws autoscaling delete-auto-scaling-group \
      --auto-scaling-group-name "$asg" \
      --force-delete --region "$AWS_REGION"

    deleted=false
    for attempt in {1..60}; do
      remaining=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names "$asg" \
        --query 'length(AutoScalingGroups)' \
        --output text --region "$AWS_REGION")
      if [[ "$remaining" == "0" ]]; then
        deleted=true
        break
      fi
      sleep 5
    done

    if [[ "$deleted" != "true" ]]; then
      echo "Timed out waiting for $asg to be deleted."
      exit 1
    fi

    if [[ -n "$instance_ids" && "$instance_ids" != "None" ]]; then
      aws ec2 wait instance-terminated \
        --instance-ids $instance_ids --region "$AWS_REGION"
    fi
  fi
  echo "$asg: deleted or already absent."
done

echo "== Deleting the ALB and its listeners =="
ALB_ARN=$(aws elbv2 describe-load-balancers \
  --query "LoadBalancers[?LoadBalancerName=='$ALB_NAME'].LoadBalancerArn | [0]" \
  --output text --region "$AWS_REGION")

if [[ -n "$ALB_ARN" && "$ALB_ARN" != "None" ]]; then
  aws elbv2 delete-load-balancer \
    --load-balancer-arn "$ALB_ARN" --region "$AWS_REGION"
  aws elbv2 wait load-balancers-deleted \
    --load-balancer-arns "$ALB_ARN" --region "$AWS_REGION"
fi

echo "== Deleting target groups =="
for tg_name in "$UPLOADER_TG_NAME" "$VIEWER_TG_NAME"; do
  tg_arn=$(aws elbv2 describe-target-groups \
    --query "TargetGroups[?TargetGroupName=='$tg_name'].TargetGroupArn | [0]" \
    --output text --region "$AWS_REGION")

  if [[ -n "$tg_arn" && "$tg_arn" != "None" ]]; then
    aws elbv2 delete-target-group \
      --target-group-arn "$tg_arn" --region "$AWS_REGION"
  fi
done

echo "== Restoring the previous lab's app-port rule =="
VPC_ID=$(aws ec2 describe-security-groups \
  --group-ids "$APP_SECURITY_GROUP_ID" \
  --query 'SecurityGroups[0].VpcId' \
  --output text --region "$AWS_REGION")

ALB_SECURITY_GROUP_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$ALB_SG_NAME" \
            "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' \
  --output text --region "$AWS_REGION")

if [[ -n "$ALB_SECURITY_GROUP_ID" &&
      "$ALB_SECURITY_GROUP_ID" != "None" ]]; then
  if result=$(aws ec2 revoke-security-group-ingress \
      --group-id "$APP_SECURITY_GROUP_ID" \
      --protocol tcp --port "$APP_PORT" \
      --source-group "$ALB_SECURITY_GROUP_ID" \
      --region "$AWS_REGION" 2>&1); then
    :
  elif [[ "$result" != *"InvalidPermission.NotFound"* ]]; then
    echo "$result" >&2
    exit 1
  fi
fi

if result=$(aws ec2 authorize-security-group-ingress \
    --group-id "$APP_SECURITY_GROUP_ID" \
    --protocol tcp --port "$APP_PORT" \
    --cidr 0.0.0.0/0 --region "$AWS_REGION" 2>&1); then
  :
elif [[ "$result" != *"InvalidPermission.Duplicate"* ]]; then
  echo "$result" >&2
  exit 1
fi

echo "== Deleting the ALB security group =="
if [[ -n "$ALB_SECURITY_GROUP_ID" &&
      "$ALB_SECURITY_GROUP_ID" != "None" ]]; then
  deleted=false
  for attempt in {1..30}; do
    if result=$(aws ec2 delete-security-group \
        --group-id "$ALB_SECURITY_GROUP_ID" \
        --region "$AWS_REGION" 2>&1); then
      deleted=true
      break
    elif [[ "$result" == *"InvalidGroup.NotFound"* ]]; then
      deleted=true
      break
    elif [[ "$result" == *"DependencyViolation"* ]]; then
      sleep 5
    else
      echo "$result" >&2
      exit 1
    fi
  done
  if [[ "$deleted" != "true" ]]; then
    echo "ALB security group still has dependencies. Retry cleanup later."
    exit 1
  fi
fi
# END ALB-ASG CLEANUP

for id_file in uploader_instance_id.txt viewer_instance_id.txt; do
  if [[ -s "$id_file" ]]; then
    instance_id=$(cat "$id_file")
    echo "Terminating $instance_id"
    aws ec2 terminate-instances \
      --instance-ids "$instance_id" --region "$AWS_REGION" >/dev/null
    aws ec2 wait instance-terminated \
      --instance-ids "$instance_id" --region "$AWS_REGION"
    rm "$id_file"
  fi
done

echo "== Deleting launch templates =="
for template in "$UPLOADER_LT_NAME" "$VIEWER_LT_NAME"; do
  if aws ec2 describe-launch-templates \
      --launch-template-names "$template" \
      --region "$AWS_REGION" >/dev/null 2>&1; then
    aws ec2 delete-launch-template \
      --launch-template-name "$template" --region "$AWS_REGION"
  fi
done

remove_role() {
  local role="$1" profile="$2"

  if aws iam get-instance-profile \
      --instance-profile-name "$profile" >/dev/null 2>&1; then
    attached=$(aws iam get-instance-profile \
      --instance-profile-name "$profile" \
      --query 'InstanceProfile.Roles[].RoleName' --output text)
    for attached_role in $attached; do
      aws iam remove-role-from-instance-profile \
        --instance-profile-name "$profile" --role-name "$attached_role"
    done
    aws iam delete-instance-profile --instance-profile-name "$profile"
  fi

  if aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    policies=$(aws iam list-role-policies --role-name "$role" \
      --query 'PolicyNames[]' --output text)
    for policy in $policies; do
      aws iam delete-role-policy --role-name "$role" --policy-name "$policy"
    done
    aws iam delete-role --role-name "$role"
  fi
}

echo "== Removing lab profiles and roles =="
remove_role "$UPLOADER_ROLE_NAME" "$UPLOADER_PROFILE_NAME"
remove_role "$VIEWER_ROLE_NAME" "$VIEWER_PROFILE_NAME"

echo "== Emptying and deleting the lab bucket =="
if aws s3api head-bucket \
    --bucket "$BUCKET_NAME" --region "$AWS_REGION" 2>/dev/null; then
  aws s3 rm "s3://$BUCKET_NAME" --recursive --region "$AWS_REGION"
  aws s3api delete-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION"
fi

echo "Cleanup complete."
