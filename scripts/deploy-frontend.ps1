# Build the Angular app, upload it to the S3 bucket and refresh CloudFront.
# Needs Node, the AWS CLI, and `terraform apply` done in infra/terraform.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tf = Join-Path $root "infra\terraform"

function Get-TfOutput($name) {
    $value = terraform "-chdir=$tf" output -raw $name
    if (-not $?) { throw "Could not read Terraform output '$name'; run 'terraform apply' first." }
    $value
}

$bucket = "s3://" + (Get-TfOutput frontend_bucket)
$distribution = Get-TfOutput cloudfront_distribution_id

Push-Location (Join-Path $root "frontend")
try {
    npm ci
    if (-not $?) { throw "npm ci failed." }
    npm run build
    if (-not $?) { throw "The Angular build failed." }
} finally { Pop-Location }
$dist = Join-Path $root "frontend\dist\browser"

# File names are content-hashed, so everything but index.html can be cached for a year.
# Content types are set explicitly: Windows can report .js as text/plain, which browsers reject.
function Invoke-Aws {
    aws @args
    if (-not $?) { throw "aws $($args -join ' ') failed." }
}
$immutable = "public,max-age=31536000,immutable"
Invoke-Aws s3 sync $dist $bucket --exclude "*" --include "*.js" --content-type "text/javascript" --cache-control $immutable
Invoke-Aws s3 sync $dist $bucket --exclude "*" --include "*.css" --content-type "text/css" --cache-control $immutable
Invoke-Aws s3 sync $dist $bucket --exclude "*.js" --exclude "*.css" --exclude "index.html" --cache-control $immutable
# index.html last, so it never points at files that aren't uploaded yet.
Invoke-Aws s3 cp (Join-Path $dist "index.html") "$bucket/index.html" --content-type "text/html" --cache-control "no-cache"
# Everything is uploaded by now, so this only deletes files left over from earlier builds.
Invoke-Aws s3 sync $dist $bucket --delete --size-only
Invoke-Aws cloudfront create-invalidation --distribution-id $distribution --paths "/" "/index.html" | Out-Null
Write-Host "Frontend deployed: $(Get-TfOutput app_url)"
