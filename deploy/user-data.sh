#!/bin/bash
# EC2 user data: runs once, as root, on the instance's first boot.
# provision.ps1 fills in the __PLACEHOLDERS__ before launching.
# Output goes to /var/log/transit-bootstrap.log on the instance.
set -euxo pipefail
exec > /var/log/transit-bootstrap.log 2>&1

dnf install -y git
git clone https://github.com/loganongithub/bay-area-transit-predictor.git /opt/src
cd /opt/src

# Config for the logger and uploader. API keys are NOT here: they live in
# SSM Parameter Store and load-keys.sh copies them in (see below).
cat > /etc/transit-logger.env <<'EOF'
AGENCY_511=AC
INTERVAL_511_S=150
INTERVAL_BART_S=30
INTERVAL_ACT_S=30
S3_BUCKET=__BUCKET__
AWS_DEFAULT_REGION=__REGION__
HEALTHCHECK_URL=__HEALTHCHECK__
API_511_KEY=
API_ACT_TOKEN=
EOF
chmod 600 /etc/transit-logger.env

bash deploy/setup.sh

# Pick up API keys if they are already stored; harmless if not.
/opt/transit-logger/load-keys.sh || true
