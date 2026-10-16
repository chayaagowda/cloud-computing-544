#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

set -a
source .env
set +a
export AWS_PAGER=""

for file in {uploader,viewer}-app/{app.js,package.json}; do
  [[ -f "$file" ]] || { echo "Missing file: $file"; exit 1; }
done

echo "== S3 bucket =="
if aws s3api head-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION" 2>/dev/null; then
  echo "Bucket already exists."
else
  aws s3api create-bucket \
    --bucket "$BUCKET_NAME" \
    --region "$AWS_REGION" \
    --create-bucket-configuration LocationConstraint="$AWS_REGION"
fi

cat > trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
EOF

setup_role() {
  local role="$1" profile="$2" policy_file="$3"

  if ! aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    aws iam create-role \
      --role-name "$role" \
      --assume-role-policy-document file://trust-policy.json
  fi

  aws iam put-role-policy \
    --role-name "$role" \
    --policy-name "${role}-policy" \
    --policy-document "file://$policy_file"

  if ! aws iam get-instance-profile \
      --instance-profile-name "$profile" >/dev/null 2>&1; then
    aws iam create-instance-profile --instance-profile-name "$profile"
  fi

  local attached
  attached=$(aws iam get-instance-profile \
    --instance-profile-name "$profile" \
    --query 'InstanceProfile.Roles[0].RoleName' --output text)

  if [[ "$attached" == "None" ]]; then
    aws iam add-role-to-instance-profile \
      --instance-profile-name "$profile" --role-name "$role"
  elif [[ "$attached" != "$role" ]]; then
    echo "Unexpected role in $profile: $attached"
    exit 1
  fi
}

echo "== Uploader IAM role =="
cat > uploader-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject"],
    "Resource": "arn:aws:s3:::${BUCKET_NAME}/shared.txt"
  }]
}
EOF
setup_role "$UPLOADER_ROLE_NAME" "$UPLOADER_PROFILE_NAME" uploader-policy.json

echo "== Viewer IAM role =="
cat > viewer-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/shared.txt"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::${BUCKET_NAME}"
    }
  ]
}
EOF
setup_role "$VIEWER_ROLE_NAME" "$VIEWER_PROFILE_NAME" viewer-policy.json

echo "Waiting 15 seconds for IAM propagation..."
sleep 15

build_template() {
  local app_name="$1" profile="$2" template="$3"
  local app_b64 pkg_b64 userdata_b64

  app_b64=$(base64 < "${app_name}-app/app.js" | tr -d '\r\n')
  pkg_b64=$(base64 < "${app_name}-app/package.json" | tr -d '\r\n')

  cat > "user-data-${app_name}.sh" << EOF
#!/bin/bash
set -e
dnf update -y
dnf install -y nodejs npm
mkdir -p /opt/app
echo "$app_b64" | base64 -d > /opt/app/app.js
echo "$pkg_b64" | base64 -d > /opt/app/package.json
cd /opt/app
npm install --omit=dev

cat > /etc/systemd/system/${app_name}.service << 'SERVICE'
[Unit]
Description=${app_name} App
After=network-online.target
Wants=network-online.target
[Service]
Environment=BUCKET_NAME=$BUCKET_NAME
Environment=AWS_REGION=$AWS_REGION
Environment=PORT=$APP_PORT
WorkingDirectory=/opt/app
ExecStart=/usr/bin/node /opt/app/app.js
Restart=always
User=ec2-user
[Install]
WantedBy=multi-user.target
SERVICE

chown -R ec2-user:ec2-user /opt/app
systemctl daemon-reload
systemctl enable --now ${app_name}.service
EOF

  userdata_b64=$(base64 < "user-data-${app_name}.sh" | tr -d '\r\n')

  cat > "${app_name}-lt-data.json" << EOF
{
  "ImageId": "$AMI_ID",
  "InstanceType": "$INSTANCE_TYPE",
  "KeyName": "$KEY_NAME",
  "SecurityGroupIds": ["$APP_SECURITY_GROUP_ID"],
  "IamInstanceProfile": {"Name": "$profile"},
  "UserData": "$userdata_b64",
  "TagSpecifications": [{
    "ResourceType": "instance",
    "Tags": [{"Key": "Name", "Value": "${app_name}-app"}]
  }]
}
EOF

  if aws ec2 describe-launch-templates \
      --launch-template-names "$template" \
      --region "$AWS_REGION" >/dev/null 2>&1; then
    aws ec2 create-launch-template-version \
      --launch-template-name "$template" \
      --source-version 1 \
      --launch-template-data "file://${app_name}-lt-data.json" \
      --region "$AWS_REGION" >/dev/null
  else
    aws ec2 create-launch-template \
      --launch-template-name "$template" \
      --launch-template-data "file://${app_name}-lt-data.json" \
      --region "$AWS_REGION" >/dev/null
  fi
}

echo "== Building launch templates =="
build_template uploader "$UPLOADER_PROFILE_NAME" "$UPLOADER_LT_NAME"
build_template viewer "$VIEWER_PROFILE_NAME" "$VIEWER_LT_NAME"


echo "== Looking up subnets in the app security group's VPC =="
VPC_ID=$(aws ec2 describe-security-groups \
  --group-ids "$APP_SECURITY_GROUP_ID" \
  --query 'SecurityGroups[0].VpcId' \
  --output text --region "$AWS_REGION")

SUBNET_A=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
            "Name=availability-zone,Values=$AZ_A" \
  --query 'Subnets[0].SubnetId' \
  --output text --region "$AWS_REGION")

SUBNET_B=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
            "Name=availability-zone,Values=$AZ_B" \
  --query 'Subnets[0].SubnetId' \
  --output text --region "$AWS_REGION")

for subnet in "$SUBNET_A" "$SUBNET_B"; do
  if [[ -z "$subnet" || "$subnet" == "None" ]]; then
    echo "Missing required subnet. Check AZ_A and AZ_B."
    exit 1
  fi
done

# Ignore only the specific expected error, not other AWS failures.
allow_ingress() {
  local result
  if result=$(aws ec2 authorize-security-group-ingress "$@" \
      --region "$AWS_REGION" 2>&1); then
    return 0
  elif [[ "$result" == *"InvalidPermission.Duplicate"* ]]; then
    return 0
  else
    echo "$result" >&2
    return 1
  fi
}

echo "== ALB security group =="
ALB_SECURITY_GROUP_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$ALB_SG_NAME" \
            "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' \
  --output text --region "$AWS_REGION")

if [[ -z "$ALB_SECURITY_GROUP_ID" ||
      "$ALB_SECURITY_GROUP_ID" == "None" ]]; then
  ALB_SECURITY_GROUP_ID=$(aws ec2 create-security-group \
    --group-name "$ALB_SG_NAME" \
    --description "ALB listener access for the resilience lab" \
    --vpc-id "$VPC_ID" \
    --query GroupId --output text --region "$AWS_REGION")
fi

allow_ingress --group-id "$ALB_SECURITY_GROUP_ID" \
  --protocol tcp --port "$UPLOADER_LISTENER_PORT" \
  --cidr 0.0.0.0/0

allow_ingress --group-id "$ALB_SECURITY_GROUP_ID" \
  --protocol tcp --port "$VIEWER_LISTENER_PORT" \
  --cidr 0.0.0.0/0

echo "== Restricting app traffic to the ALB =="
allow_ingress --group-id "$APP_SECURITY_GROUP_ID" \
  --protocol tcp --port "$APP_PORT" \
  --source-group "$ALB_SECURITY_GROUP_ID"

if result=$(aws ec2 revoke-security-group-ingress \
    --group-id "$APP_SECURITY_GROUP_ID" \
    --protocol tcp --port "$APP_PORT" --cidr 0.0.0.0/0 \
    --region "$AWS_REGION" 2>&1); then
  echo "Removed public app-port access."
elif [[ "$result" != *"InvalidPermission.NotFound"* ]]; then
  echo "$result" >&2
  exit 1
fi

echo "== Uploader target group =="
UPLOADER_TG_ARN=$(aws elbv2 describe-target-groups \
  --query "TargetGroups[?TargetGroupName=='$UPLOADER_TG_NAME'].TargetGroupArn | [0]" \
  --output text --region "$AWS_REGION")

if [[ -z "$UPLOADER_TG_ARN" || "$UPLOADER_TG_ARN" == "None" ]]; then
  UPLOADER_TG_ARN=$(aws elbv2 create-target-group \
    --name "$UPLOADER_TG_NAME" \
    --protocol HTTP --port "$APP_PORT" \
    --vpc-id "$VPC_ID" --target-type instance \
    --health-check-protocol HTTP --health-check-path / \
    --health-check-interval-seconds 10 \
    --health-check-timeout-seconds 5 \
    --healthy-threshold-count 2 --unhealthy-threshold-count 2 \
    --query 'TargetGroups[0].TargetGroupArn' \
    --output text --region "$AWS_REGION")
fi

aws elbv2 modify-target-group \
  --target-group-arn "$UPLOADER_TG_ARN" \
  --health-check-protocol HTTP --health-check-path / \
  --health-check-interval-seconds 10 \
  --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 --unhealthy-threshold-count 2 \
  --region "$AWS_REGION" >/dev/null

echo "== Viewer target group =="
VIEWER_TG_ARN=$(aws elbv2 describe-target-groups \
  --query "TargetGroups[?TargetGroupName=='$VIEWER_TG_NAME'].TargetGroupArn | [0]" \
  --output text --region "$AWS_REGION")

if [[ -z "$VIEWER_TG_ARN" || "$VIEWER_TG_ARN" == "None" ]]; then
  VIEWER_TG_ARN=$(aws elbv2 create-target-group \
    --name "$VIEWER_TG_NAME" \
    --protocol HTTP --port "$APP_PORT" \
    --vpc-id "$VPC_ID" --target-type instance \
    --health-check-protocol HTTP --health-check-path / \
    --health-check-interval-seconds 10 \
    --health-check-timeout-seconds 5 \
    --healthy-threshold-count 2 --unhealthy-threshold-count 2 \
    --query 'TargetGroups[0].TargetGroupArn' \
    --output text --region "$AWS_REGION")
fi

aws elbv2 modify-target-group \
  --target-group-arn "$VIEWER_TG_ARN" \
  --health-check-protocol HTTP --health-check-path / \
  --health-check-interval-seconds 10 \
  --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 --unhealthy-threshold-count 2 \
  --region "$AWS_REGION" >/dev/null

echo "== Application Load Balancer =="
ALB_ARN=$(aws elbv2 describe-load-balancers \
  --query "LoadBalancers[?LoadBalancerName=='$ALB_NAME'].LoadBalancerArn | [0]" \
  --output text --region "$AWS_REGION")

if [[ -z "$ALB_ARN" || "$ALB_ARN" == "None" ]]; then
  ALB_ARN=$(aws elbv2 create-load-balancer \
    --name "$ALB_NAME" \
    --subnets "$SUBNET_A" "$SUBNET_B" \
    --security-groups "$ALB_SECURITY_GROUP_ID" \
    --scheme internet-facing --type application \
    --query 'LoadBalancers[0].LoadBalancerArn' \
    --output text --region "$AWS_REGION")
fi

echo "Waiting for the ALB to become active..."
aws elbv2 wait load-balancer-available \
  --load-balancer-arns "$ALB_ARN" --region "$AWS_REGION"

echo "== Uploader listener =="
UPLOADER_LISTENER_ARN=$(aws elbv2 describe-listeners \
  --load-balancer-arn "$ALB_ARN" \
  --query "Listeners[?Port==\`$UPLOADER_LISTENER_PORT\`].ListenerArn | [0]" \
  --output text --region "$AWS_REGION")

if [[ -z "$UPLOADER_LISTENER_ARN" ||
      "$UPLOADER_LISTENER_ARN" == "None" ]]; then
  aws elbv2 create-listener \
    --load-balancer-arn "$ALB_ARN" \
    --protocol HTTP --port "$UPLOADER_LISTENER_PORT" \
    --default-actions "Type=forward,TargetGroupArn=$UPLOADER_TG_ARN" \
    --region "$AWS_REGION" >/dev/null
else
  aws elbv2 modify-listener \
    --listener-arn "$UPLOADER_LISTENER_ARN" \
    --default-actions "Type=forward,TargetGroupArn=$UPLOADER_TG_ARN" \
    --region "$AWS_REGION" >/dev/null
fi

echo "== Viewer listener =="
VIEWER_LISTENER_ARN=$(aws elbv2 describe-listeners \
  --load-balancer-arn "$ALB_ARN" \
  --query "Listeners[?Port==\`$VIEWER_LISTENER_PORT\`].ListenerArn | [0]" \
  --output text --region "$AWS_REGION")

if [[ -z "$VIEWER_LISTENER_ARN" ||
      "$VIEWER_LISTENER_ARN" == "None" ]]; then
  aws elbv2 create-listener \
    --load-balancer-arn "$ALB_ARN" \
    --protocol HTTP --port "$VIEWER_LISTENER_PORT" \
    --default-actions "Type=forward,TargetGroupArn=$VIEWER_TG_ARN" \
    --region "$AWS_REGION" >/dev/null
else
  aws elbv2 modify-listener \
    --listener-arn "$VIEWER_LISTENER_ARN" \
    --default-actions "Type=forward,TargetGroupArn=$VIEWER_TG_ARN" \
    --region "$AWS_REGION" >/dev/null
fi

echo "== Uploader Auto Scaling Group =="
UPLOADER_ASG_EXISTS=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$UPLOADER_ASG_NAME" \
  --query 'length(AutoScalingGroups)' \
  --output text --region "$AWS_REGION")

if [[ "$UPLOADER_ASG_EXISTS" == "0" ]]; then
  aws autoscaling create-auto-scaling-group \
    --auto-scaling-group-name "$UPLOADER_ASG_NAME" \
    --launch-template "LaunchTemplateName=$UPLOADER_LT_NAME,Version=\$Latest" \
    --min-size 1 --max-size 1 --desired-capacity 1 \
    --vpc-zone-identifier "$SUBNET_A,$SUBNET_B" \
    --target-group-arns "$UPLOADER_TG_ARN" \
    --health-check-type ELB --health-check-grace-period 120 \
    --region "$AWS_REGION"
else
  aws autoscaling update-auto-scaling-group \
    --auto-scaling-group-name "$UPLOADER_ASG_NAME" \
    --launch-template "LaunchTemplateName=$UPLOADER_LT_NAME,Version=\$Latest" \
    --min-size 1 --max-size 1 --desired-capacity 1 \
    --vpc-zone-identifier "$SUBNET_A,$SUBNET_B" \
    --health-check-type ELB --health-check-grace-period 120 \
    --region "$AWS_REGION"
  aws autoscaling attach-load-balancer-target-groups \
    --auto-scaling-group-name "$UPLOADER_ASG_NAME" \
    --target-group-arns "$UPLOADER_TG_ARN" \
    --region "$AWS_REGION"
fi

echo "== Viewer Auto Scaling Group =="
VIEWER_ASG_EXISTS=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$VIEWER_ASG_NAME" \
  --query 'length(AutoScalingGroups)' \
  --output text --region "$AWS_REGION")

if [[ "$VIEWER_ASG_EXISTS" == "0" ]]; then
  aws autoscaling create-auto-scaling-group \
    --auto-scaling-group-name "$VIEWER_ASG_NAME" \
    --launch-template "LaunchTemplateName=$VIEWER_LT_NAME,Version=\$Latest" \
    --min-size 1 --max-size 1 --desired-capacity 1 \
    --vpc-zone-identifier "$SUBNET_A,$SUBNET_B" \
    --target-group-arns "$VIEWER_TG_ARN" \
    --health-check-type ELB --health-check-grace-period 120 \
    --region "$AWS_REGION"
else
  aws autoscaling update-auto-scaling-group \
    --auto-scaling-group-name "$VIEWER_ASG_NAME" \
    --launch-template "LaunchTemplateName=$VIEWER_LT_NAME,Version=\$Latest" \
    --min-size 1 --max-size 1 --desired-capacity 1 \
    --vpc-zone-identifier "$SUBNET_A,$SUBNET_B" \
    --health-check-type ELB --health-check-grace-period 120 \
    --region "$AWS_REGION"
  aws autoscaling attach-load-balancer-target-groups \
    --auto-scaling-group-name "$VIEWER_ASG_NAME" \
    --target-group-arns "$VIEWER_TG_ARN" \
    --region "$AWS_REGION"
fi

ALB_DNS_NAME=$(aws elbv2 describe-load-balancers \
  --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].DNSName' \
  --output text --region "$AWS_REGION")

cat > stack_outputs.env <<OUTPUTS
ALB_ARN=$ALB_ARN
ALB_DNS_NAME=$ALB_DNS_NAME
ALB_SECURITY_GROUP_ID=$ALB_SECURITY_GROUP_ID
UPLOADER_TG_ARN=$UPLOADER_TG_ARN
VIEWER_TG_ARN=$VIEWER_TG_ARN
SUBNET_A=$SUBNET_A
SUBNET_B=$SUBNET_B
OUTPUTS

echo "== Deployment resources created =="
echo "Uploader: http://$ALB_DNS_NAME:$UPLOADER_LISTENER_PORT/"
echo "Viewer:   http://$ALB_DNS_NAME:$VIEWER_LISTENER_PORT/"
echo "Allow several minutes for app installation and healthy targets."
