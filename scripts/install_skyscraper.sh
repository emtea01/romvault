#!/usr/bin/env bash
# Installs Skyscraper (muldjord/skyscraper) inside this LXC and sets up
# ROM Vault's cover-only artwork recipe.
#
# IMPORTANT: unlike the main ROM Vault app, this NEEDS internet access
# from inside the container -- it builds Skyscraper from source and,
# later, talks to screenscraper.fr to fetch art. Make sure this
# container's network config allows outbound internet before running it.
#
# Run this as root inside the container:
#   pct enter <vmid>
#   cd /root/romvault/scripts
#   chmod +x install_skyscraper.sh
#   ./install_skyscraper.sh
set -euo pipefail

echo ">> Installing build dependencies (this can take a few minutes)..."
apt-get update
apt-get install -y git build-essential qtbase5-dev qtbase5-dev-tools \
  libqt5sql5-sqlite qt5-qmake curl ca-certificates

echo ">> Building and installing Skyscraper via the official installer script..."
mkdir -p /root/skysource
cd /root/skysource
curl -fsSL https://raw.githubusercontent.com/muldjord/skyscraper/master/update_skyscraper.sh | bash

if ! command -v Skyscraper >/dev/null 2>&1; then
  echo "!! Skyscraper doesn't appear on PATH after install."
  echo "!! Check /root/skysource for build errors, or try:"
  echo "!!   ln -s /root/skysource/Skyscraper/Skyscraper /usr/local/bin/Skyscraper"
  exit 1
fi

echo ">> Installing ROM Vault's cover-only artwork recipe..."
mkdir -p /root/.skyscraper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SCRIPT_DIR}/skyscraper-artwork.xml" /root/.skyscraper/romvault-artwork.xml

echo ">> Writing baseline config.ini (safe defaults; scrape_art.sh overrides per-run paths)..."
cat > /root/.skyscraper/config.ini <<'EOF'
[main]
frontend="emulationstation"
artworkXml="/root/.skyscraper/romvault-artwork.xml"
region="us"
regionPrios="us,eu,wor,jp"
lang="en"
langPrios="en"
EOF

echo ""
echo ">> Done. Skyscraper is installed."
echo ">> Optional but recommended: create a free account at https://www.screenscraper.fr"
echo "   (raises your rate limit a lot) then set credentials before scraping:"
echo "     export SCREENSCRAPER_USER=youruser"
echo "     export SCREENSCRAPER_PASS=yourpass"
echo ">> Then run: ./scrape_art.sh nes snes gba     (or: ./scrape_art.sh all)"
