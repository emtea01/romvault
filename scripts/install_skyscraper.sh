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
  libqt5sql5-sqlite qt5-qmake curl ca-certificates sudo

echo ">> Building and installing Skyscraper via the official installer script..."
mkdir -p /root/skysource
cd /root/skysource
curl -fsSL https://raw.githubusercontent.com/muldjord/skyscraper/master/update_skyscraper.sh | bash

# Check the actual install path directly rather than `command -v`/PATH --
# the official installer puts the binary at /usr/local/bin/Skyscraper,
# but `pct exec`'s minimal PATH (/sbin:/bin:/usr/sbin:/usr/bin) doesn't
# include /usr/local/bin, so a PATH-based check gives a false negative
# even when the install succeeded.
if [ ! -x /usr/local/bin/Skyscraper ]; then
  echo "!! Skyscraper doesn't appear to be at /usr/local/bin/Skyscraper after install."
  echo "!! Check /root/skysource for build errors. If it landed somewhere else, find it with:"
  echo "!!   find / -xdev -name Skyscraper -type f 2>/dev/null"
  exit 1
fi

echo ">> Skyscraper installed at /usr/local/bin/Skyscraper"

echo ">> Installing ROM Vault's cover-only artwork recipe (root -- manual CLI use)..."
mkdir -p /root/.skyscraper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SCRIPT_DIR}/skyscraper-artwork.xml" /root/.skyscraper/romvault-artwork.xml

echo ">> Writing baseline config.ini (root)..."
cat > /root/.skyscraper/config.ini <<'EOF'
[main]
frontend="emulationstation"
artworkXml="/root/.skyscraper/romvault-artwork.xml"
region="us"
regionPrios="us,eu,wor,jp"
lang="en"
langPrios="en"
EOF

# The app itself runs as the unprivileged 'romvault' service user (see
# romvault.service), which can't read/write anything under /root -- so it
# needs its own separate Skyscraper config/cache location, owned by that
# user, for the automatic scraping triggered by [ RESCAN ].
echo ">> Installing the same recipe for the app's automatic scraping (romvault user)..."
mkdir -p /opt/romvault/skyscraper-home/.skyscraper
cp "${SCRIPT_DIR}/skyscraper-artwork.xml" /opt/romvault/skyscraper-home/.skyscraper/romvault-artwork.xml
cat > /opt/romvault/skyscraper-home/.skyscraper/config.ini <<'EOF'
[main]
frontend="emulationstation"
artworkXml="/opt/romvault/skyscraper-home/.skyscraper/romvault-artwork.xml"
region="us"
regionPrios="us,eu,wor,jp"
lang="en"
langPrios="en"
EOF
chown -R romvault:romvault /opt/romvault/skyscraper-home
mkdir -p /opt/romvault/skyscraper-work
chown -R romvault:romvault /opt/romvault/skyscraper-work

echo ""
echo ">> Done. Skyscraper is installed."
echo ">> Optional but recommended: create a free account at https://www.screenscraper.fr"
echo "   (raises your rate limit a lot), then set it from the app itself:"
echo "   log in as an admin -> [ SETTINGS ] -> ScreenScraper username/password."
echo ">> Automatic scraping now runs on every [ RESCAN ] -- no manual step needed."
echo ">> (scripts/scrape_art.sh is still available for manual CLI use as root, if preferred.)"
