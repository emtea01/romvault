# ROM Vault — iteration 5

A lightweight, self-hosted retro ROM browser with a green/amber CRT
terminal look. Browses a ROM library on a NAS, supports search/filter,
download, and in-browser play via EmulatorJS. License: MIT (see
`LICENSE`).

For browsing and playing ROMs already legally owned as backups — same
spirit as EmulatorJS, RetroArch, and the open-source emulation tooling
this project builds on.

**Systems:** NES, SNES, N64, GBA, NDS — play in-browser + download.
**GameCube & Wii** — download only. No browser-capable (WASM) Dolphin
core runs at usable speed yet, so in-browser GC/Wii emulation isn't
realistic currently.

**New in iteration 5:**
- **Automatic, incremental box art scraping.** Skyscraper runs in-app,
  triggered by **[ RESCAN ]** — no separate terminal session. Only scrapes
  ROMs without art yet, so repeat runs stay fast and never re-touch
  already-matched titles.
- **Admin Settings screen** for the ROM/box art folder paths and
  ScreenScraper credentials — no env vars or config files to edit by
  hand; changes take effect immediately.
- **Skyscraper installs by default** as part of the standard install — no
  separate manual step (see the internet-access note under "Box art via
  Skyscraper" below).

**Carried forward from iteration 4:**
- **Multi-user accounts.** Favorites, recently-played, and save states are
  tied to individual accounts and follow across devices, instead of
  living in one browser's local storage.
- **One-line Proxmox installer** (`proxmox-install.sh`) that creates the
  container, sets up the NAS mounts, and deploys from this repo in one go
  — see "One-line install" below.

---

## 0. What this deploys

A small Flask app (Python), served by gunicorn, running as a systemd
service inside a single unprivileged Debian LXC container. ROMs aren't
stored in the container — they're read live from wherever the NAS share
is mounted, so the container itself stays tiny (1 vCPU / 512MB RAM / a
few GB disk is plenty).

Expected ROM folder layout on the NAS (one folder per system):

```
roms/
  nes/    *.nes
  snes/   *.sfc  *.smc
  n64/    *.z64  *.n64  *.v64
  gba/    *.gba
  nds/    *.nds
  gc/     *.iso  *.rvz  *.gcm
  wii/    *.iso  *.rvz  *.wbfs
```

If folders are named differently, either rename them to match, or open an
issue — the folder-to-system mapping could be made configurable.

### Box art (optional)

Drop images next to the ROMs, under a `boxart/` folder at the same level
as the system folders, using the **same base filename** as the ROM:

```
roms/
  nes/       Legend_of_Zelda,The.nes
  boxart/
    nes/     Legend_of_Zelda,The.png
```

Supported image types: `.png` `.jpg` `.jpeg` `.webp`. Any ROM without a
matching image falls back to a themed cartridge icon — nothing breaks if
only some games have art. Hit **[ RESCAN ]** in the UI after adding art.

---

## One-line install (recommended for a fresh setup)

With a NAS share already mounted on the Proxmox host (step 1 below),
`proxmox-install.sh` automates steps 2-6 into one command — it creates
the container, sets up both NAS bind mounts, clones this repo, and runs
the installer inside it:

```bash
NAS_ROMS_PATH=/mnt/pve/roms-nas bash -c "$(curl -fsSL https://raw.githubusercontent.com/YOUR-USERNAME/romvault/main/proxmox-install.sh)"
```

Replace `YOUR-USERNAME` with the fork/repo this is pushed to (see "Fork
and host on GitHub" below — the script clones from that repo, not a
canonical upstream one).

**Status: not yet run end-to-end against a real Proxmox host.** Written
carefully against documented `pct`/`pveam` behavior, but should be
treated as a first draft rather than battle-tested. Test on a throwaway
VMID before relying on it. Steps 1-6 below are the same process spelled
out manually, as a fallback.

---

## 1. Mount the NAS share on the Proxmox host

The cleanest way to give an **unprivileged** LXC access to a NAS share is
to mount the share on the Proxmox host itself, then bind-mount that path
into the container. This avoids giving the container mount capabilities.

SSH into the Proxmox host, then:

**NFS share:**
```bash
mkdir -p /mnt/pve/roms-nas
apt-get install -y nfs-common
mount -t nfs 192.168.1.50:/volume1/roms /mnt/pve/roms-nas
```

**SMB/CIFS share:**
```bash
mkdir -p /mnt/pve/roms-nas
apt-get install -y cifs-utils
mount -t cifs //192.168.1.50/roms /mnt/pve/roms-nas \
  -o username=youruser,password=yourpass,uid=100000,gid=100000,file_mode=0644,dir_mode=0755
```
(`uid=100000,gid=100000` maps ownership to the first unprivileged
container's root — adjust if the container's UID mapping differs.)

Make it persistent by adding the equivalent line to `/etc/fstab` on the
Proxmox host (use `_netdev` for network mounts), then `mount -a` to confirm.

---

## 2. Create the LXC container

Easiest via the Proxmox web UI: **Datacenter → node → Create CT**

- **Template:** Debian 12 (bookworm)
- **Unprivileged container:** yes (leave checked)
- **CPU:** 1 core
- **Memory:** 512 MB (1 GB for headroom)
- **Disk:** 4–8 GB
- **Network:** DHCP or static IP on the LAN, bridged to vmbr0

Or via CLI on the Proxmox host (adjust `100` to a free VMID and the
storage/bridge names to match the target setup):

```bash
pct create 100 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname romvault \
  --unprivileged 1 \
  --cores 1 \
  --memory 512 \
  --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp
```

---

## 3. Bind-mount the NAS share into the container

Still on the Proxmox host, with the container stopped or running:

```bash
pct set 100 -mp0 /mnt/pve/roms-nas,mp=/mnt/roms,ro=1
```

`ro=1` mounts it read-only inside the container — a good default since
the app only needs to read ROMs (downloads/streaming), never write to
the NAS.

**If Skyscraper art-scraping will be used**, add a *second* bind mount
pointed at just the `boxart` subfolder, mounted read-write. Linux happily
layers a read-write mount on top of a subtree of a read-only one, so ROM
files stay protected while art scraping can write:

```bash
pct set 100 -mp1 /mnt/pve/roms-nas/boxart,mp=/mnt/roms/boxart,ro=0
```

(If box art is only ever added manually from the NAS/PC side and
scraping from inside the container is never used, this can be skipped —
see "Box art (optional)" above.)

Start the container:
```bash
pct start 100
```

---

## 4. Copy the app into the container

Copy the whole `romvault/` folder to the Proxmox host, then push it into
the container. From the Proxmox host:

```bash
# copy the project folder onto the Proxmox host first, e.g. via scp:
#   scp -r romvault root@<proxmox-host>:/root/

# pct push only copies single files, not whole folders -- bundle it first:
tar czf /root/romvault.tar.gz -C /root romvault
pct push 100 /root/romvault.tar.gz /root/romvault.tar.gz
pct exec 100 -- tar xzf /root/romvault.tar.gz -C /root/
pct exec 100 -- rm /root/romvault.tar.gz
```

(Alternatively, `pct enter 100` and use `scp`/`wget`/`git clone` from
inside the container directly.)

---

## 5. Install and start the service

Enter the container and run the installer:

```bash
pct enter 100
cd /root/romvault
chmod +x install.sh
./install.sh
```

This installs Python, sets up a virtualenv, creates a dedicated
`romvault` system user, installs the systemd service, and starts it — and
by default also installs Skyscraper for automatic box art scraping (see
section 7).

Check it's running:
```bash
systemctl status romvault
```

---

## 6. Open it

From any browser on the LAN:
```
http://<container-ip>:5000
```

Find the container's IP with `pct exec 100 -- ip -4 addr show eth0` (or
check the Proxmox UI summary tab for the container).

---

## 7. Box art via Skyscraper (automatic, built in)

[Skyscraper](https://github.com/muldjord/skyscraper) installs
automatically as part of `install.sh` (and therefore also via
`proxmox-install.sh`) — no separate manual step on a fresh install. For
an **existing** container from before this was automatic, see "Existing
container?" below.

**One deliberate exception to how this project otherwise runs:** the app
itself needs zero internet access, but installing Skyscraper does — it
needs to reach the internet during install (to build itself) and later
(to query screenscraper.fr). Confirm the container's network allows
outbound access before installing.

### How it works day to day

Once installed, there's no separate scraping step. Every time
**[ RESCAN ]** is hit:
1. The ROM library re-indexes (as before).
2. ROMs still missing box art are identified.
3. If any are missing **and** Skyscraper is installed, a scrape starts
   automatically for just those — never re-processing titles that already
   matched, so repeat rescans stay fast.
4. Once scraping finishes, the library refreshes again automatically so
   the new art appears — no second RESCAN click needed.

The header shows live progress while this runs (`FETCHING BOX ART (NES):
3/12`), the same way it shows "INDEXING LIBRARY..." during a filesystem
scan.

### ScreenScraper credentials (recommended)

Scraping works anonymously, but a free account at
[screenscraper.fr](https://www.screenscraper.fr) raises the rate limit
substantially — worth setting up for anything beyond a handful of games.
Configured from the app itself, not env vars: log in as an admin, open
**[ SETTINGS ]**, and fill in the ScreenScraper username/password. The
ROMs root folder and box art folder can also be changed from that same
screen — changes apply immediately, no restart needed.

### Existing container? (upgrading from before this was automatic)

If Skyscraper was already installed manually via the old
`scripts/install_skyscraper.sh` process, nothing changes — it's already
installed and the automatic-on-rescan behavior picks it up as-is. To add
it to a container that skipped that step, without recreating the
container:
```bash
pct exec <vmid> -- bash /opt/romvault/scripts/install_skyscraper.sh
```

### Plan B: Skraper.net (manual, from a PC)

For anyone who'd rather not have Skyscraper build inside the container at
all (e.g. to avoid the internet-access requirement), or if Skyscraper has
trouble matching a stubborn platform, **Skraper.net** is a solid
fallback — a Windows (Mac beta) GUI app that runs from a PC instead of
inside the LXC:

1. Map/mount the NAS `roms` share as a drive on that PC.
2. In Skraper: point **ROM root folder** at that share, choose systems.
3. In the **Media** tab, set:
   - **Media type:** Box (2D)
   - **Output folder:** `%ROMROOTFOLDER%\boxart\%SYSTEM%\`
     (whichever variable Skraper offers for the platform short name)
   - **Naming:** match the **ROM filename**, not the scraped game title —
     this matters, since ROM Vault matches art by exact ROM base filename
4. Run the scrape, then hit **[ RESCAN ]** in ROM Vault. (Skyscraper can
   also be skipped entirely at install time with `SKIP_SKYSCRAPER=1
   ./install.sh` if Skraper.net is the only plan.)

---

## Faster deploys after the first setup

The tar/`pct push`/extract dance in steps 4-5 is only needed the *first*
time, because the container isn't reachable directly yet. Once it's up,
updates can go straight to the container's own IP over SSH — much faster
for iterating.

**One-time check:** confirm SSH reaches the container directly (most
Proxmox LXC templates have this enabled by default):
```bash
ssh root@<container-ip>
```
If that connects, updates are ready to go. If not, run `apt-get install
-y openssh-server` inside the container once (`pct enter <vmid>` first).

**From then on**, updates are one command:

- **Windows:** edit `$DefaultContainerIp` at the top of `deploy.ps1` once
  (in this project folder) to the container's IP, then run:
  ```powershell
  .\deploy.ps1
  ```
  Copies the folder straight to the container and restarts the service in
  one step — no Proxmox host, no tar, no `pct push`.

- **Mac/Linux:** the equivalent is:
  ```bash
  scp -r romvault root@<container-ip>:/root/romvault-staging
  ssh root@<container-ip> "cp -r /root/romvault-staging/. /opt/romvault/ && chown -R romvault:romvault /opt/romvault && systemctl restart romvault"
  ```
  **Important:** the systemd service actually runs from `/opt/romvault`,
  not wherever `scp` lands — that's why this stages to a throwaway folder
  first, then merges into `/opt/romvault` (which also holds `venv/` and
  `instance/`, which must *not* be overwritten; a plain `cp -r source/.
  dest/` merge leaves those alone). Worth saving as a two-line
  `deploy.sh` for frequent use.

- **From a phone, or anywhere without a local copy of the repo:** since
  the repo is on GitHub, the container can pull its own update directly
  — no file transfer from a client machine needed at all. From a shell
  *inside* the container (the Proxmox web UI's Shell tab works fine from
  a phone browser, no app install needed):
  ```bash
  bash <(curl -fsSL https://raw.githubusercontent.com/YOUR-USERNAME/romvault/main/redeploy.sh)
  ```
  (Set `GITHUB_REPO` inside `redeploy.sh` once, same as `proxmox-install.sh`,
  so the URL above works without an env var every time.) This clones fresh
  from GitHub, merges into `/opt/romvault` the same safe way as above, and
  restarts the service — one command, no Windows/Mac tooling required.

---

## Accounts, favorites, recent, and save states

The very first visit lands on a **SETUP** screen instead of the vault —
create a username and password there (min. 8 characters). This becomes
the first **admin** account.

Every person gets their own account rather than sharing one password:

- **Adding more accounts:** as an admin, click **[ USERS ]** in the top
  bar to open the admin panel — add an account per person, optionally
  granting admin (which just means they can also manage users).
- **Favorites, recently-played, and save states are all tied to the
  account**, stored server-side, and follow to any device logged into —
  not per-browser local storage like before.
- **Save states specifically:** hitting Save State in the player's own
  menu (gear icon, bottom of screen) syncs to the account automatically;
  opening the same game on another device auto-loads it. **Status:** the
  auto-load side uses a documented EmulatorJS option
  (`EJS_loadStateURL`) that hasn't been verified end-to-end against a
  live install — the save-and-upload half is tested and works; the
  auto-load-on-a-different-device half should work per EmulatorJS's docs
  but is worth confirming on first use.
- **Password reset:** no self-service "forgot password" flow yet — an
  admin resets a password by deleting and recreating that user's account
  from the admin panel (this loses that person's favorites/recent/saves,
  since those are tied to the account ID). If every admin is locked out,
  wipe the whole user database and start over:
  ```bash
  systemctl stop romvault
  rm /opt/romvault/instance/romvault.db
  systemctl start romvault
  ```
  The SETUP screen appears again on next visit.

Login attempts are rate-limited (5 tries per 5 minutes, then a 60-second
lockout per IP) — resets on service restart, and is meant to slow down
casual guessing on a LAN, not withstand a determined attacker from the
open internet.

---

## Fork and host on GitHub

This project is meant to live in its own repo so changes can be tracked
and the one-line installer used. From the `romvault` folder:

```bash
git init
git add .
git commit -m "ROM Vault iteration 5"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/romvault.git
git push -u origin main
```

An empty repo needs to exist on GitHub first — either via the web UI
(github.com → New repository → don't initialize with a
README/license/gitignore, since this folder already has its own), or
with the GitHub CLI:
```bash
gh repo create romvault --public --source=. --remote=origin --push
```

After that, edit `GITHUB_REPO` near the top of `proxmox-install.sh` (and
push that change) so the one-line installer points at the right repo by
default instead of needing the env var every time.

**Not committed** (already excluded via `.gitignore`): the `instance/`
folder holds the session secret key and the SQLite database with every
user's password hash, favorites, and save states — none of that belongs
in git history, even a private repo.

---

## Notes & known limitations

- **GC/Wii are download-only.** No usable browser emulator core exists
  for these yet — the PLAY button is intentionally disabled for them.
- **EmulatorJS loads from a public CDN** (`cdn.emulatorjs.org`) in the
  *player's browser*, not from the container — so the container itself
  needs no internet access, only the browser does, on Play.
- The library list is cached for 5 minutes by default (configurable via
  the `CACHE_TTL_SECONDS` env var); **[ RESCAN ]** in the UI (or `POST
  /api/rescan`) forces an immediate refresh after adding new ROMs. Box
  art lookups are indexed per-system with a handful of directory
  listings rather than checked file-by-file, so this stays fast even at
  several thousand ROMs.
- The container mounts the NAS **read-only** by default — the app never
  writes to the ROM share. With the Skyscraper scraping tool, only the
  `boxart` subfolder gets a (separate, explicit) read-write mount — ROM
  files stay protected either way.
- Multi-user accounts are suitable for a trusted home LAN (rate-limited
  login, hashed passwords, per-account data isolation). For exposure
  beyond a LAN, put it behind a reverse proxy with TLS (e.g. Caddy or
  nginx with a Let's Encrypt cert) — the app itself doesn't do HTTPS.
- **Cross-device save-state auto-loading is the least-tested feature in
  this release** — see the note under "Accounts, favorites, recent, and
  save states" above.
- **`proxmox-install.sh` hasn't been run against a real Proxmox host** —
  written carefully, not battle-tested. Test on a throwaway VMID first.
- **Actual scraping/matching against ScreenScraper hasn't been tested
  against a real ROM set.** The `scraper.py` orchestration (filtering to
  unmatched ROMs, symlink staging, output flattening,
  auto-rescan-after-scrape) is fully tested against a stand-in Skyscraper
  binary, so that logic is solid. What's *not* verified is Skyscraper's
  actual matching behavior/flags against real ROM filenames — if scraping
  runs but finds nothing, that's worth reporting as an issue so the CLI
  flags in `scraper.py` can be adjusted.
- Settings changes (ROMs path, box art path) take effect immediately, but
  only affect the container they're set on — if `ROMS_PATH`/
  `BOXART_PATH` env vars are also set in `romvault.service`, the Settings
  screen's values take priority over those (env vars are only the
  fallback default for a fresh install with nothing configured yet).

## Ideas for iteration 6

- "Continue playing" — surface last few played titles on the home screen
- Self-service password reset (currently admin-only via the users panel)
- Per-user access levels (e.g. a kid-safe view with a curated subset)
- Reverse proxy + HTTPS config included out of the box
- Save-state screenshots (EmulatorJS provides one alongside each save;
  currently ignored — could show a thumbnail on hover)
- A "scrape now" button in Settings, to trigger a scrape without waiting
  for the next RESCAN

## Contributing

Issues and pull requests welcome. The codebase is small and
single-file-per-concern on purpose (`app.py` for routes, `db.py` for
storage, `scraper.py` for box art) — keep changes in that spirit where
reasonable.
