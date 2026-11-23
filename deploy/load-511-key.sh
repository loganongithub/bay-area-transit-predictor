#!/bin/bash
# Copy the 511 API key from SSM Parameter Store into the logger's env file,
# then restart the logger. Run as root on the instance (set-511-key.ps1 does
# this remotely). Never prints the key.
set -euo pipefail

ENV_FILE=/etc/transit-logger.env
PARAM=/transit-logger/api-511-key
REGION=$(sed -n 's/^AWS_DEFAULT_REGION=//p' "$ENV_FILE")

if ! KEY=$(aws ssm get-parameter --region "$REGION" --name "$PARAM" \
      --with-decryption --query Parameter.Value --output text 2>/dev/null); then
  echo "no $PARAM in Parameter Store yet; logging BART only"
  exit 0
fi

# 511 keys are plain alphanumeric/dashes; refuse anything else rather than
# risk mangling the env file.
if ! [[ "$KEY" =~ ^[A-Za-z0-9-]+$ ]]; then
  echo "stored key has unexpected characters; not applied" >&2
  exit 1
fi

sed -i "s|^API_511_KEY=.*|API_511_KEY=$KEY|" "$ENV_FILE"
systemctl restart transit-logger
echo "511 key loaded, logger restarted"
