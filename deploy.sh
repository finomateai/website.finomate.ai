#!/usr/bin/env bash
set -euo pipefail

S3_BUCKET="your-bucket-name"

echo "Running build..."
bash build.sh

echo "Deploying to s3://$S3_BUCKET ..."
aws s3 sync dist/ "s3://$S3_BUCKET" --delete --cache-control "max-age=86400"

echo "Deploy complete."
echo "NOTE: Ensure 404.html is configured as the S3 error document in your bucket settings."
