#!/usr/bin/env bash
# ROM Vault install script
# Run this INSIDE the Proxmox LXC container (as root), after copying the
# romvault/ project folder to /root/romvault (see README.md).
set -euo pipefail

APP_DIR="/opt/romvault"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ">> Installing system packages..."
apt-get update
apt-get install -y python3 python3-venv python3-pip

echo ">> Creating service user..."
if ! id romvault >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin romvault
fi

echo ">> Copying app to ${APP_DIR}..."
mkdir -p "${APP_DIR}"
cp -r "${SRC_DIR}/app.py" "${SRC_DIR}/db.py" "${SRC_DIR}/requirements.txt" "${SRC_DIR}/templates" "${SRC_DIR}/static" "${APP_DIR}/"

echo ">> Creating virtualenv + installing Python deps..."
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo ">> Ensuring /mnt/roms mount point exists..."
mkdir -p /mnt/roms

echo ">> Creating instance directory (session key + auth config)..."
mkdir -p "${APP_DIR}/instance"

echo ">> Setting permissions..."
chown -R romvault:romvault "${APP_DIR}"

echo ">> Installing systemd service..."
cp "${SRC_DIR}/romvault.service" /etc/systemd/system/romvault.service
systemctl daemon-reload
systemctl enable romvault
systemctl restart romvault

echo ""
echo ">> Done. Checking service status:"
systemctl --no-pager status romvault || true
echo ""
echo "If /mnt/roms is mounted, visit: http://<container-ip>:5000"
echo "First visit will show a SETUP screen to create the shared password."
