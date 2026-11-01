#!/usr/bin/env bash
# Build the backend image, push it to ECR and restart the container on the EC2 instance.
# Needs Docker running, the AWS CLI, and `terraform apply` done in infra/terraform.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TF="$ROOT/infra/terraform"

REPO=$(terraform -chdir="$TF" output -raw ecr_repository_url)  # <account>.dkr.ecr.<region>.amazonaws.com/<name>
INSTANCE=$(terraform -chdir="$TF" output -raw backend_instance_id)
REGION=$(echo "$REPO" | cut -d. -f4)
IMAGE="$REPO:latest"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -t "$IMAGE" "$ROOT/backend"
docker push "$IMAGE"

echo "Restarting the backend on $INSTANCE"
COMMAND_ID=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" \
  --document-name AWS-RunShellScript --parameters commands=/usr/local/bin/knowledge-agent-deploy \
  --query Command.CommandId --output text)
aws ssm wait command-executed --region "$REGION" --command-id "$COMMAND_ID" --instance-id "$INSTANCE" || {
  aws ssm get-command-invocation --region "$REGION" --command-id "$COMMAND_ID" --instance-id "$INSTANCE" \
    --query "[Status,StandardErrorContent]" --output text
  exit 1
}
echo "Backend deployed."
