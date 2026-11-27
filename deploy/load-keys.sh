#!/bin/bash
# Copy API keys from SSM Parameter Store into the logger's env file, then
# restart the logger. Run as root on the instance (set-keys.ps1 does this
# remotely). Never prints a key.
#
#   /transit-logger/api-511-key  -> API_511_KEY   (511.org)
#   /transit-logger/act-token    -> API_ACT_TOKEN (AC Transit's own API)
set -euo pipefail

ENV_FILE=/etc/transit-logger.env
REGION=$(sed -n 's/^AWS_DEFAULT_REGION=//p' "$ENV_FILE")
changed=0

load() {  # load <parameter name> <env var>
  local param=$1 var=$2 value
  if ! value=$(aws ssm get-parameter --region "$REGION" --name "$param" \
        --with-decryption --query Parameter.Value --output text 2>/dev/null); then
    echo "$param not in Parameter Store; $var left unchanged"
    return 0
  fi
  # Keys are plain alphanumeric/dashes; refuse anything else rather than
  # risk mangling the env file.
  if ! [[ "$value" =~ ^[A-Za-z0-9-]+$ ]]; then
    echo "$param has unexpected characters; not applied" >&2
    return 1
  fi
  if grep -q "^$var=" "$ENV_FILE"; then
    sed -i "s|^$var=.*|$var=$value|" "$ENV_FILE"
  else
    echo "$var=$value" >> "$ENV_FILE"
  fi
  echo "$var loaded"
  changed=1
}

load /transit-logger/api-511-key API_511_KEY
load /transit-logger/act-token API_ACT_TOKEN

if [ "$changed" = 1 ]; then
  systemctl restart transit-logger
  echo "logger restarted"
fi
