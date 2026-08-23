#!/usr/bin/env bash
#
# ROM Vault -- one-line Proxmox installer.
# Run this ON THE PROXMOX HOST (not inside a container):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/YOUR-USERNAME/romvault/main/proxmox-install.sh)"
#
# This creates a new unprivileged LXC container, bind-mounts your NAS ROM
# share into it, clones ROM Vault from GitHub, and runs its installer
# inside the container -- the same steps documented manually in README.md,
# automated into one script.
#
# NOTE ON TESTING: this script was written carefully against documented
# `pct`/`pveam` behavior, but wasn't run end-to-end against a real Proxmox
# host while building it (no Proxmox host available in the build
# environment). Try it on a throwaway VMID first and report back anything
# that doesn't match reality -- treat it as a strong first draft, not a
# battle-tested tool yet.
set -euo pipefail

# ---------------------------------------------------------------------------
# Config -- edit this before pushing to your repo, or override any of it
# with environment variables at run time, e.g.:
#   NAS_ROMS_PATH=/mnt/pve/my-nas/roms bash -c "$(curl ...)"
# ---------------------------------------------------------------------------
GITHUB_REPO="${GITHUB_REPO:-https://github.com/CHANGE-ME/romvault.git}"

CTID="${CTID:-}"                          # empty = auto-pick next free ID
HOSTNAME="${HOSTNAME:-romvault}"
CORES="${CORES:-1}"
MEMORY_MB="${MEMORY_MB:-512}"
DISK_GB="${DISK_GB:-6}"
STORAGE="${STORAGE:-local-lvm}"
BRIDGE="${BRIDGE:-vmbr0}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
ROOT_PASSWORD="${ROOT_PASSWORD:-}"        # empty = auto-generate, printed at the end

# Path ON THE PROXMOX HOST where your NAS roms share is already mounted
# (see README.md step 1 -- this script does not mount the NAS itself,
# only bind-mounts an existing host mount into the new container).
NAS_ROMS_PATH="${NAS_ROMS_PATH:-}"

# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "!! Run this as root on the Proxmox host." >&2
    exit 1
fi

if ! command -v pct >/dev/null 2>&1; then
    echo "!! 'pct' not found -- this doesn't look like a Proxmox host." >&2
    exit 1
fi

if [ "$GITHUB_REPO" = "https://github.com/CHANGE-ME/romvault.git" ]; then
    echo "!! Set GITHUB_REPO first -- either edit this script before pushing it to your repo,"
    echo "!! or run: GITHUB_REPO=https://github.com/you/romvault.git bash -c \"\$(curl ...)\""
    exit 1
fi

if [ -z "$NAS_ROMS_PATH" ]; then
    read -rp "Path on THIS Proxmox host where your NAS roms share is mounted (e.g. /mnt/pve/roms-nas): " NAS_ROMS_PATH
fi
if [ ! -d "$NAS_ROMS_PATH" ]; then
    echo "!! $NAS_ROMS_PATH doesn't exist or isn't mounted. Mount your NAS share there first (see README.md step 1)." >&2
    exit 1
fi

if [ -z "$ROOT_PASSWORD" ]; then
    ROOT_PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)"
    echo ">> No ROOT_PASSWORD given -- generated one (shown again at the end)."
fi

# ---------------------------------------------------------------------------
# Pick a VMID and make sure the Debian 12 template is available
# ---------------------------------------------------------------------------
if [ -z "$CTID" ]; then
    CTID="$(pvesh get /cluster/nextid)"
fi
echo ">> Using CTID $CTID"

echo ">> Checking for the Debian 12 LXC template..."
pveam update >/dev/null 2>&1 || true
TEMPLATE="$(pveam available --section system 2>/dev/null | grep -o 'debian-12-standard[^ ]*' | sort -V | tail -1 || true)"
if [ -z "$TEMPLATE" ]; then
    TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
    echo "!! Couldn't auto-detect the latest template name, falling back to $TEMPLATE"
    echo "!! If this fails, run 'pveam available' yourself and set TEMPLATE manually."
fi
if [ ! -f "/var/lib/vz/template/cache/${TEMPLATE}" ]; then
    echo ">> Downloading $TEMPLATE ..."
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

# ---------------------------------------------------------------------------
# Create and configure the container
# ---------------------------------------------------------------------------
echo ">> Creating container $CTID ..."
pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    --hostname "$HOSTNAME" \
    --unprivileged 1 \
    --cores "$CORES" \
    --memory "$MEMORY_MB" \
    --rootfs "${STORAGE}:${DISK_GB}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
    --password "$ROOT_PASSWORD" \
    --features "nesting=1"

echo ">> Bind-mounting $NAS_ROMS_PATH -> /mnt/roms (read-only) ..."
pct set "$CTID" -mp0 "${NAS_ROMS_PATH},mp=/mnt/roms,ro=1"

echo ">> Bind-mounting ${NAS_ROMS_PATH}/boxart -> /mnt/roms/boxart (read-write, for scraping) ..."
mkdir -p "${NAS_ROMS_PATH}/boxart"
pct set "$CTID" -mp1 "${NAS_ROMS_PATH}/boxart,mp=/mnt/roms/boxart,ro=0"

echo ">> Starting container ..."
pct start "$CTID"

echo ">> Waiting for network..."
for i in $(seq 1 30); do
    if pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

# ---------------------------------------------------------------------------
# Deploy ROM Vault from GitHub and run its own installer
# ---------------------------------------------------------------------------
echo ">> Installing git and cloning $GITHUB_REPO ..."
pct exec "$CTID" -- bash -c "apt-get update -qq && apt-get install -y -qq git openssh-server"
pct exec "$CTID" -- git clone --depth 1 "$GITHUB_REPO" /root/romvault

echo ">> Running ROM Vault's own installer inside the container ..."
pct exec "$CTID" -- bash -c "chmod +x /root/romvault/install.sh && /root/romvault/install.sh"

echo ">> Allowing root SSH password login (adjust later if you set up SSH keys instead) ..."
pct exec "$CTID" -- bash -c "echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config; echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config; systemctl restart ssh"

CT_IP="$(pct exec "$CTID" -- hostname -I | awk '{print $1}')"

echo ""
echo "======================================================================"
echo " ROM Vault is up."
echo "   URL:            http://${CT_IP}:5000"
echo "   Container VMID: ${CTID}"
echo "   Root password:  ${ROOT_PASSWORD}"
echo ""
echo " First visit will show a SETUP screen to create your first (admin)"
echo " account -- favorites, recently-played, and save states are all tied"
echo " to that account from here on."
echo ""
echo " For updates going forward, use deploy.ps1 (Windows) or the manual"
echo " git-pull + resync steps in README.md -- see 'Faster deploys'."
echo "======================================================================"
