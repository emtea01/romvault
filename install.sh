#!/usr/bin/env bash
# ROM Vault install script
# Run this INSIDE the Proxmox LXC container (as root), after copying the
# romvault/ project folder to /root/romvault (see README.md).
#
# Installs the core app AND Skyscraper (box art scraping) by default --
# set SKIP_SKYSCRAPER=1 to skip the Skyscraper build if you don't want it
# or don't want this install to need internet access.
#
# NOTE: with Skyscraper included, this install now needs outbound internet
# access (to build Skyscraper and, later, to actually query ScreenScraper)
# -- unlike earlier versions of this project where only the app itself was
# installed and no internet was required at all.
set -euo pipefail

APP_DIR="/opt/romvault"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_SKYSCRAPER="${SKIP_SKYSCRAPER:-0}"

echo ">> Installing system packages..."
apt-get update
apt-get install -y python3 python3-venv python3-pip

echo ">> Creating service user..."
if ! id romvault >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin romvault
fi

echo ">> Copying app to ${APP_DIR}..."
mkdir -p "${APP_DIR}"
cp -r "${SRC_DIR}/app.py" "${SRC_DIR}/db.py" "${SRC_DIR}/scraper.py" \
  "${SRC_DIR}/requirements.txt" "${SRC_DIR}/templates" "${SRC_DIR}/static" "${APP_DIR}/"

echo ">> Creating virtualenv + installing Python deps..."
python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo ">> Ensuring /mnt/roms mount point exists..."
mkdir -p /mnt/roms

echo ">> Creating instance directory (session key + database)..."
mkdir -p "${APP_DIR}/instance"

echo ">> Setting permissions..."
chown -R romvault:romvault "${APP_DIR}"

echo ">> Installing systemd service..."
cp "${SRC_DIR}/romvault.service" /etc/systemd/system/romvault.service
systemctl daemon-reload
systemctl enable romvault
systemctl restart romvault

if [ "$SKIP_SKYSCRAPER" != "1" ]; then
  echo ""
  echo ">> Installing Skyscraper (box art scraping) -- this needs internet"
  echo ">> access and can take a few minutes to build. Set SKIP_SKYSCRAPER=1"
  echo ">> before running this script to skip it."
  if bash "${SRC_DIR}/scripts/install_skyscraper.sh"; then
    echo ">> Skyscraper installed. Box art will be scraped automatically on RESCAN"
    echo ">> once you set ScreenScraper credentials (optional) in [ SETTINGS ]."
  else
    echo "!! Skyscraper install failed -- ROM Vault itself is still fully"
    echo "!! functional without it. See scripts/install_skyscraper.sh to retry"
    echo "!! manually, or add box art via the manual method in README.md."
  fi
else
  echo ">> SKIP_SKYSCRAPER=1 set -- skipping Skyscraper install."
fi

echo ""
echo ">> Done. Checking service status:"
systemctl --no-pager status romvault || true
echo ""
echo "If /mnt/roms is mounted, visit: http://<container-ip>:5000"
echo "First visit will show a SETUP screen to create your first (admin) account."
