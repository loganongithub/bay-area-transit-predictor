#!/bin/bash
# Move every finished snapshot from local disk to S3, keeping the same paths.
# "mv" deletes each local file only after its upload succeeds, so a failed
# run just leaves files for the next run. Credentials come from the EC2
# instance role; no keys live on the server.
set -euo pipefail

RAW=/var/lib/transit-logger/raw
: "${S3_BUCKET:?S3_BUCKET not set in /etc/transit-logger.env}"

[ -d "$RAW" ] || exit 0
aws s3 mv "$RAW" "s3://$S3_BUCKET/raw" --recursive --only-show-errors

# Tidy empty day folders, but only old ones so we never race the logger.
find "$RAW" -mindepth 1 -type d -empty -mmin +120 -delete
