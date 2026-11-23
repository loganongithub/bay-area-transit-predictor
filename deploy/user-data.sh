#!/bin/bash
# EC2 user data: runs once, as root, on the instance's first boot.
# provision.ps1 fills in the __PLACEHOLDERS__ before launching.
# Output goes to /var/log/transit-bootstrap.log on the instance.
set -euxo pipefail
exec > /var/log/transit-bootstrap.log 2>&1

dnf install -y git
git clone https://github.com/loganongithub/bay-area-transit-predictor.git /opt/src
cd /opt/src

# Config for the logger and uploader. The 511 key is NOT here: it lives in
# SSM Parameter Store and load-511-key.sh copies it in (see below).
cat > /etc/transit-logger.env <<'EOF'
AGENCY_511=AC
INTERVAL_511_S=150
INTERVAL_BART_S=30
S3_BUCKET=__BUCKET__
AWS_DEFAULT_REGION=__REGION__
HEALTHCHECK_URL=__HEALTHCHECK__
API_511_KEY=
EOF
chmod 600 /etc/transit-logger.env

bash deploy/setup.sh

# Pick up the 511 key if it's already stored; harmless if not.
/opt/transit-logger/load-511-key.sh || true
