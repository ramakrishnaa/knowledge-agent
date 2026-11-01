#!/usr/bin/env bash
# Build the Angular app, upload it to the S3 bucket and refresh CloudFront.
# Needs Node, the AWS CLI, and `terraform apply` done in infra/terraform.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TF="$ROOT/infra/terraform"

BUCKET="s3://$(terraform -chdir="$TF" output -raw frontend_bucket)"
DISTRIBUTION=$(terraform -chdir="$TF" output -raw cloudfront_distribution_id)

(cd "$ROOT/frontend" && npm ci && npm run build)
DIST="$ROOT/frontend/dist/browser"

# File names are content-hashed, so everything but index.html can be cached for a year.
IMMUTABLE="public,max-age=31536000,immutable"
aws s3 sync "$DIST" "$BUCKET" --exclude "*" --include "*.js" --content-type "text/javascript" --cache-control "$IMMUTABLE"
aws s3 sync "$DIST" "$BUCKET" --exclude "*" --include "*.css" --content-type "text/css" --cache-control "$IMMUTABLE"
aws s3 sync "$DIST" "$BUCKET" --exclude "*.js" --exclude "*.css" --exclude "index.html" --cache-control "$IMMUTABLE"
# index.html last, so it never points at files that aren't uploaded yet.
aws s3 cp "$DIST/index.html" "$BUCKET/index.html" --content-type "text/html" --cache-control "no-cache"
# Everything is uploaded by now, so this only deletes files left over from earlier builds.
aws s3 sync "$DIST" "$BUCKET" --delete --size-only
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --paths "/" "/index.html" >/dev/null
echo "Frontend deployed: $(terraform -chdir="$TF" output -raw app_url)"
