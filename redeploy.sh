#!/usr/bin/env bash
# Redeploys ROM Vault from GitHub, run INSIDE the container itself.
# Useful when deploy.ps1 isn't available (e.g. deploying from a phone via
# the Proxmox web Shell, or any SSH client) -- no Windows tooling needed,
# just a shell inside the container.
#
# Usage (inside the container):
#   curl -fsSL https://raw.githubusercontent.com/YOUR-USERNAME/romvault/main/redeploy.sh | bash
# or, if the repo is already cloned somewhere:
#   bash redeploy.sh
set -euo pipefail

GITHUB_REPO="${GITHUB_REPO:-https://github.com/YOUR-USERNAME/romvault.git}"
STAGING_DIR="/root/romvault-staging"
APP_DIR="/opt/romvault"

if [ "$GITHUB_REPO" = "https://github.com/YOUR-USERNAME/romvault.git" ]; then
  echo "!! Set GITHUB_REPO first -- either edit this script before pushing it,"
  echo "!! or run: GITHUB_REPO=https://github.com/you/romvault.git bash redeploy.sh"
  exit 1
fi

echo ">> Cloning latest from ${GITHUB_REPO}..."
rm -rf "${STAGING_DIR}"
git clone --depth 1 "${GITHUB_REPO}" "${STAGING_DIR}"

echo ">> Syncing into ${APP_DIR} (venv/ and instance/ are left untouched)..."
cp -r "${STAGING_DIR}/." "${APP_DIR}/"
chown -R romvault:romvault "${APP_DIR}"

echo ">> Refreshing systemd service file (in case it changed) and restarting..."
cp "${APP_DIR}/romvault.service" /etc/systemd/system/romvault.service
systemctl daemon-reload
systemctl restart romvault
systemctl is-active romvault

rm -rf "${STAGING_DIR}"

echo ">> Done."
