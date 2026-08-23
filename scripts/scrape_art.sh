#!/usr/bin/env bash
# Scrapes box art for one or more systems and drops it into ROM Vault's
# expected layout: $ROMS_PATH/boxart/<system>/<rom-filename-stem>.<ext>
#
# Usage:
#   ./scrape_art.sh nes snes gba        # scrape specific systems
#   ./scrape_art.sh all                 # scrape every supported system
#
# Env vars (optional):
#   ROMS_PATH             default: /mnt/roms
#   SCREENSCRAPER_USER    ScreenScraper.fr username (recommended, not required)
#   SCREENSCRAPER_PASS    ScreenScraper.fr password
#
# Re-running is safe and incremental -- Skyscraper caches what it's
# already found, so a repeat run mostly just picks up new/renamed ROMs.
set -euo pipefail

ROMS_PATH="${ROMS_PATH:-/mnt/roms}"
BOXART_ROOT="${ROMS_PATH}/boxart"
WORK_DIR="/root/skyscraper-work"
ARTWORK_XML="/root/.skyscraper/romvault-artwork.xml"

# Called by full path deliberately, not by bare name -- `pct exec`'s PATH
# is minimal (/sbin:/bin:/usr/sbin:/usr/bin) and doesn't include
# /usr/local/bin, where the official installer puts Skyscraper. Bare
# `Skyscraper` works fine in an interactive `pct enter` shell but silently
# fails to resolve under `pct exec`.
SKYSCRAPER_BIN="${SKYSCRAPER_BIN:-/usr/local/bin/Skyscraper}"

ALL_SYSTEMS=(nes snes n64 gba nds gc wii)

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <system> [system...]  |  $0 all"
  echo "Supported systems: ${ALL_SYSTEMS[*]}"
  exit 1
fi

if [ "$1" == "all" ]; then
  SYSTEMS=("${ALL_SYSTEMS[@]}")
else
  SYSTEMS=("$@")
fi

if [ ! -x "$SKYSCRAPER_BIN" ]; then
  echo "!! Skyscraper isn't at $SKYSCRAPER_BIN. Run install_skyscraper.sh first,"
  echo "!! or set SKYSCRAPER_BIN if it landed somewhere else."
  exit 1
fi

if [ ! -w "${BOXART_ROOT}" ] && ! mkdir -p "${BOXART_ROOT}" 2>/dev/null; then
  echo "!! Can't write to ${BOXART_ROOT}."
  echo "!! If your NAS mount is read-only inside this container, add a"
  echo "!! second, writable bind mount just for the boxart/ subfolder --"
  echo "!! see README.md 'Making the boxart folder writable' section."
  exit 1
fi

CRED_ARGS=()
if [ -n "${SCREENSCRAPER_USER:-}" ] && [ -n "${SCREENSCRAPER_PASS:-}" ]; then
  CRED_ARGS=(-u "${SCREENSCRAPER_USER}:${SCREENSCRAPER_PASS}")
else
  echo ">> No SCREENSCRAPER_USER/PASS set -- scraping anonymously (slower, lower rate limit)."
fi

for system in "${SYSTEMS[@]}"; do
  rom_dir="${ROMS_PATH}/${system}"
  if [ ! -d "${rom_dir}" ]; then
    echo ">> Skipping '${system}': no folder at ${rom_dir}"
    continue
  fi

  echo ""
  echo "=== ${system}: gathering data from ScreenScraper ==="
  work_platform="${WORK_DIR}/${system}"
  mkdir -p "${work_platform}/media"

  "$SKYSCRAPER_BIN" -p "${system}" -s screenscraper \
    -i "${rom_dir}" \
    "${CRED_ARGS[@]}" \
    --flags unattend \
    -t 4

  echo "=== ${system}: generating cover art ==="
  "$SKYSCRAPER_BIN" -p "${system}" \
    -i "${rom_dir}" \
    -g "${work_platform}" \
    -o "${work_platform}/media" \
    -a "${ARTWORK_XML}" \
    --flags forcefilename,nobrackets,unattend,skipexistingcovers

  echo "=== ${system}: copying art into ${BOXART_ROOT}/${system} ==="
  mkdir -p "${BOXART_ROOT}/${system}"
  # Skyscraper nests output under its own subfolder (e.g. media/covers/).
  # We don't rely on the exact subfolder name -- just flatten whatever
  # image files it produced into ROM Vault's expected flat layout.
  find "${work_platform}/media" -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) -print0 \
    | while IFS= read -r -d '' img; do
        cp -f "${img}" "${BOXART_ROOT}/${system}/$(basename "${img}")"
      done

  count=$(find "${BOXART_ROOT}/${system}" -type f | wc -l)
  echo "=== ${system}: done (${count} art files in ${BOXART_ROOT}/${system}) ==="
done

echo ""
echo ">> All requested systems processed."
echo ">> Hit [ RESCAN ] in the ROM Vault UI to pick up the new art."
