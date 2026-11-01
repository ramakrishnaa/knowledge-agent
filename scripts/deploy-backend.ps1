# Build the backend image, push it to ECR and restart the container on the EC2 instance.
# Needs Docker running, the AWS CLI, and `terraform apply` done in infra/terraform.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tf = Join-Path $root "infra\terraform"

function Get-TfOutput($name) {
    $value = terraform "-chdir=$tf" output -raw $name
    if (-not $?) { throw "Could not read Terraform output '$name'; run 'terraform apply' first." }
    $value
}

$repo = Get-TfOutput ecr_repository_url        # <account>.dkr.ecr.<region>.amazonaws.com/<name>
$instance = Get-TfOutput backend_instance_id
$region = ($repo -split '\.')[3]
$image = "${repo}:latest"

# Windows PowerShell re-encodes text piped between native programs, which corrupts the token; let cmd do the pipe.
$registry = ($repo -split '/')[0]
cmd /c "aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $registry"
if ($LASTEXITCODE -ne 0) { throw "docker login to ECR failed." }
docker build --platform linux/amd64 -t $image (Join-Path $root "backend")
if (-not $?) { throw "Building the backend image failed." }
docker push $image
if (-not $?) { throw "Pushing $image failed." }

Write-Host "Restarting the backend on $instance"
$commandId = aws ssm send-command --region $region --instance-ids $instance `
    --document-name AWS-RunShellScript --parameters commands=/usr/local/bin/knowledge-agent-deploy `
    --query Command.CommandId --output text
if (-not $?) { throw "Sending the restart command failed." }
aws ssm wait command-executed --region $region --command-id $commandId --instance-id $instance
$status = aws ssm get-command-invocation --region $region --command-id $commandId --instance-id $instance `
    --query "[Status,StandardErrorContent]" --output text
if (-not ($status -match '^Success')) { throw "Restart failed: $status" }
Write-Host "Backend deployed."
