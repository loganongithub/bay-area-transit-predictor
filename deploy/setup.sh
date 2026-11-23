#!/bin/bash
# One-time server setup on Amazon Linux 2023. Run from the repo root:
#   sudo bash deploy/setup.sh
# Safe to re-run after a git pull to deploy a new logger version.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi

id transitlog >/dev/null 2>&1 || useradd --system --no-create-home --shell /sbin/nologin transitlog

install -d -o transitlog -g transitlog /var/lib/transit-logger
install -d /opt/transit-logger
install -m 0644 logger/logger.py /opt/transit-logger/logger.py
install -m 0755 deploy/upload.sh /opt/transit-logger/upload.sh
install -m 0700 deploy/load-511-key.sh /opt/transit-logger/load-511-key.sh

if [ ! -f /etc/transit-logger.env ]; then
  install -m 0600 -o transitlog -g transitlog .env.example /etc/transit-logger.env
  echo ">>> Created /etc/transit-logger.env. Edit it (API_511_KEY, S3_BUCKET), then re-run this script."
  exit 0
fi

install -m 0644 deploy/transit-logger.service /etc/systemd/system/
install -m 0644 deploy/transit-upload.service /etc/systemd/system/
install -m 0644 deploy/transit-upload.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now transit-logger.service transit-upload.timer
systemctl restart transit-logger.service

echo ">>> Running. Check with: journalctl -u transit-logger -f"
