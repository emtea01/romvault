# ROM Vault — iteration 4

A lightweight, self-hosted retro ROM browser with a green/amber CRT terminal
look. Browses a ROM library on your NAS, lets you search/filter, download
any ROM, or play supported systems directly in the browser via EmulatorJS.
Source: this repo. License: MIT (see `LICENSE`).

This is a personal/home project for browsing and playing ROMs you already
legally own backups of — same spirit as EmulatorJS, RetroArch, and similar
open-source emulation tooling it's built on.

**Systems:** NES, SNES, N64, GBA, NDS — play in-browser + download.
**GameCube & Wii** — download only. There's no browser-capable (WASM)
Dolphin core that runs at usable speed yet, so in-browser GC/Wii emulation
isn't realistic right now.

**New in iteration 4:**
- **Multi-user accounts.** Favorites, recently-played, and save states are
  now tied to your account and follow you across devices, instead of
  living in one browser's local storage.
- **One-line Proxmox installer** (`proxmox-install.sh`) that creates the
  container, sets up the NAS mounts, and deploys from this repo in one go
  — see "One-line install" below.

---

## 0. What you're deploying

A small Flask app (Python), served by gunicorn, running as a systemd
service inside a single unprivileged Debian LXC container. It doesn't
store your ROMs — it reads them live from wherever you mount your NAS
share, so the container itself can stay tiny (1 vCPU / 512MB RAM / a
few GB disk is plenty).

Expected ROM folder layout on your NAS (one folder per system):

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

If your folders are named differently, either rename them to match, or
tell me and I'll make the folder-to-system mapping configurable.

### Box art (optional)

Drop images next to your ROMs, under a `boxart/` folder at the same level
as your system folders, using the **same base filename** as the ROM:

```
roms/
  nes/       Legend_of_Zelda,The.nes
  boxart/
    nes/     Legend_of_Zelda,The.png
```

Supported image types: `.png` `.jpg` `.jpeg` `.webp`. Any ROM without a
matching image just falls back to a themed cartridge icon — nothing
breaks if you only add art for some games. Hit **[ RESCAN ]** in the UI
after adding art.

---

## One-line install (recommended if starting fresh)

If you're setting this up from scratch and already have your NAS share
mounted on the Proxmox host (step 1 below), `proxmox-install.sh` automates
steps 2-6 into one command — it creates the container, sets up both NAS
bind mounts, clones this repo, and runs the installer inside it:

```bash
NAS_ROMS_PATH=/mnt/pve/roms-nas bash -c "$(curl -fsSL https://raw.githubusercontent.com/YOUR-USERNAME/romvault/main/proxmox-install.sh)"
```

Replace `YOUR-USERNAME` with wherever you've pushed this repo (see "Push
to your own GitHub" below — you'll need to have done that first, since
the script clones from your fork, not a canonical upstream one).

**This script hasn't been run end-to-end against a real Proxmox host** —
it's written carefully against documented `pct`/`pveam` behavior, but
treat it as a strong first draft. Try it on a throwaway VMID and let me
know what breaks. If you'd rather do it by hand (or something goes
wrong), steps 1-6 below are the same process spelled out manually.

---

## 1. Mount the NAS share on the Proxmox host

The cleanest way to give an **unprivileged** LXC access to a NAS share is
to mount the share on the Proxmox host itself, then bind-mount that path
into the container. This avoids giving the container mount capabilities.

SSH into the Proxmox host, then:

**If your NAS share is NFS:**
```bash
mkdir -p /mnt/pve/roms-nas
apt-get install -y nfs-common
mount -t nfs 192.168.1.50:/volume1/roms /mnt/pve/roms-nas
```

**If your NAS share is SMB/CIFS:**
```bash
mkdir -p /mnt/pve/roms-nas
apt-get install -y cifs-utils
mount -t cifs //192.168.1.50/roms /mnt/pve/roms-nas \
  -o username=youruser,password=yourpass,uid=100000,gid=100000,file_mode=0644,dir_mode=0755
```
(`uid=100000,gid=100000` maps ownership to the first unprivileged
container's root — adjust if your container's UID mapping differs.)

Make it persistent by adding the equivalent line to `/etc/fstab` on the
Proxmox host (use `_netdev` for network mounts), then `mount -a` to confirm.

---

## 2. Create the LXC container

Easiest via the Proxmox web UI: **Datacenter → node → Create CT**

- **Template:** Debian 12 (bookworm)
- **Unprivileged container:** yes (leave checked)
- **CPU:** 1 core
- **Memory:** 512 MB (1 GB if you want headroom)
- **Disk:** 4–8 GB
- **Network:** DHCP or static IP on your LAN, bridged to vmbr0

Or via CLI on the Proxmox host (adjust `100` to a free VMID and the
storage/bridge names to match your setup):

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

`ro=1` mounts it read-only inside the container, which is a good default
since this app only needs to read ROMs (downloads/streaming), not write
to your NAS.

**If you plan to use the Skyscraper art-scraping tool below**, add a
*second* bind mount pointed at just the `boxart` subfolder, mounted
read-write. Linux happily layers a read-write mount on top of a subtree
of a read-only one, so your ROMs stay protected while art scraping can
write:

```bash
pct set 100 -mp1 /mnt/pve/roms-nas/boxart,mp=/mnt/roms/boxart,ro=0
```

(If you're only ever adding box art manually from your NAS/PC and never
scraping from inside the container, you can skip this and leave
everything read-only — see the "Box art (optional)" section above.)

Start the container:
```bash
pct start 100
```

---

## 4. Copy the app into the container

From your local machine (where these files live), copy the whole
`romvault/` folder to the Proxmox host, then push it into the container.
From the Proxmox host:

```bash
# copy the project folder onto the Proxmox host first, e.g. via scp:
#   scp -r romvault root@<proxmox-host>:/root/

# pct push only copies single files, not whole folders -- bundle it first:
tar czf /root/romvault.tar.gz -C /root romvault
pct push 100 /root/romvault.tar.gz /root/romvault.tar.gz
pct exec 100 -- tar xzf /root/romvault.tar.gz -C /root/
pct exec 100 -- rm /root/romvault.tar.gz
```

(If you'd rather skip the tar step, `pct enter 100` and use `scp`/
`wget`/`git clone` from inside the container directly instead — whatever's
easiest for you.)

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
`romvault` system user, installs the systemd service, and starts it.

Check it's running:
```bash
systemctl status romvault
```

---

## 6. Open it

From any browser on your LAN:
```
http://<container-ip>:5000
```

Find the container's IP with `pct exec 100 -- ip -4 addr show eth0`
(or check the Proxmox UI summary tab for the container).

---

## 7. Box art via Skyscraper (recommended)

[Skyscraper](https://github.com/muldjord/skyscraper) is a command-line ROM
scraper that runs natively on Linux, so — unlike Skraper.net — it can run
right inside this container. It's a separate, occasional admin task, not
part of the always-on `romvault` service.

**One deliberate exception to how this project normally runs:** the main
app needs zero internet access. Scraping does not — Skyscraper needs to
reach the internet (to build itself, and later to query screenscraper.fr),
so make sure this container's network allows outbound access before you
start.

### 7a. Install it (one-time)

```bash
pct enter 100
cd /root/romvault/scripts
chmod +x install_skyscraper.sh scrape_art.sh
./install_skyscraper.sh
```

This builds Skyscraper from source via its official installer script and
drops in a ROM Vault-specific artwork recipe (`skyscraper-artwork.xml`)
that outputs a plain, undecorated cover image — no composited screenshot
collage, no 3D gamebox effect, just the cover, since that's all the vault
UI displays.

### 7b. (Recommended) get a free ScreenScraper.fr account

Scraping works anonymously, but a free account at
[screenscraper.fr](https://www.screenscraper.fr) raises your rate limit
substantially — worth doing for anything beyond a handful of games:

```bash
export SCREENSCRAPER_USER=youruser
export SCREENSCRAPER_PASS=yourpass
```

### 7c. Run it

```bash
./scrape_art.sh nes snes gba      # specific systems
./scrape_art.sh all               # everything
```

For each system, this gathers metadata + cover art into Skyscraper's own
local cache, then flattens the resulting cover images straight into
`$ROMS_PATH/boxart/<system>/<rom-filename>.png` — exactly the layout ROM
Vault already expects (see "Box art (optional)" above). It's safe to
re-run any time you add new ROMs; Skyscraper's cache means it mostly just
picks up what's new.

Hit **[ RESCAN ]** in the ROM Vault UI afterward to see the new art.

### Plan B: Skraper.net (manual, from your PC)

If you'd rather not build anything inside the container, or Skyscraper
has trouble matching a stubborn platform, **Skraper.net** is a solid
fallback — it's a Windows (Mac beta) GUI app, so it runs from your PC
instead of inside the LXC:

1. Map/mount your NAS `roms` share as a drive on that PC.
2. In Skraper: point **ROM root folder** at that share, choose your
   systems.
3. In the **Media** tab, set:
   - **Media type:** Box (2D)
   - **Output folder:** `%ROMROOTFOLDER%\boxart\%SYSTEM%\`
     (use whichever variable Skraper offers for the platform short name)
   - **Naming:** match the **ROM filename**, not the scraped game title —
     this matters, since ROM Vault matches art by exact ROM base filename
4. Run the scrape, then hit **[ RESCAN ]** in ROM Vault.

---

## Faster deploys after the first setup

The tar/`pct push`/extract dance in steps 4-5 is only really needed the
*first* time, because the container isn't reachable directly yet. Once
it's up, you can skip the Proxmox host entirely and push updates straight
to the container's own IP over SSH — much faster for iterating.

**One-time check:** confirm you can SSH directly to the container (most
Proxmox LXC templates have this enabled by default):
```bash
ssh root@<container-ip>
```
If that connects, you're set. If not, run `apt-get install -y openssh-server`
inside the container once (`pct enter <vmid>` first).

**From then on**, updates are one command:

- **Windows:** edit `$DefaultContainerIp` at the top of `deploy.ps1` once
  (in this project folder) to your container's IP, then just run:
  ```powershell
  .\deploy.ps1
  ```
  This copies the folder straight to the container and restarts the
  service in one step — no Proxmox host, no tar, no `pct push`.

- **Mac/Linux:** the equivalent is:
  ```bash
  scp -r romvault root@<container-ip>:/root/romvault-staging
  ssh root@<container-ip> "cp -r /root/romvault-staging/. /opt/romvault/ && chown -R romvault:romvault /opt/romvault && systemctl restart romvault"
  ```
  **Important:** the systemd service actually runs from `/opt/romvault`,
  not wherever you `scp` to — that's why this stages to a throwaway
  folder first, then merges it into `/opt/romvault` (which also has
  `venv/` and `instance/` in it that must *not* be overwritten; a plain
  `cp -r source/. dest/` merge leaves those alone). Feel free to save
  this as a two-line `deploy.sh` if you're on this often.

---

## Accounts, favorites, recent, and save states

On your very first visit, you'll land on a **SETUP** screen instead of the
vault — create a username and password there (min. 8 characters). This
becomes the first **admin** account.

Unlike earlier versions of this project, everyone gets their own account
now rather than sharing one password:

- **Adding more accounts:** as an admin, click **[ USERS ]** in the top
  bar to open the admin panel — add an account per person, optionally
  granting admin (which just means they can also manage users).
- **Favorites, recently-played, and save states are all tied to your
  account**, stored server-side, and follow you to any device you log
  into — not per-browser local storage like before.
- **Save states specifically:** when you hit Save State in the player's
  own menu (gear icon, bottom of screen), it's synced to your account
  automatically; opening the same game on another device auto-loads it.
  **Caveat:** the auto-load side of this uses an EmulatorJS option
  (`EJS_loadStateURL`) that's documented but I haven't personally verified
  end-to-end against a live install — the save-and-upload half is tested
  and works, the auto-load-on-a-different-device half should work per
  EmulatorJS's docs but is worth confirming yourself on the first try.
- **Forgot a password / need to reset one:** there's no self-service
  "forgot password" flow yet — an admin resets it by deleting and
  recreating that user's account from the admin panel (this does lose
  that person's favorites/recent/saves, since those are tied to the
  account ID). If everyone's locked out (e.g. you deleted the only admin),
  wipe the whole user database and start over:
  ```bash
  systemctl stop romvault
  rm /opt/romvault/instance/romvault.db
  systemctl start romvault
  ```
  You'll get the SETUP screen again on next visit.

Login attempts are rate-limited (5 tries per 5 minutes, then a 60-second
lockout per IP) — this resets if the service restarts, and it's meant to
slow down casual guessing on your LAN, not to withstand a determined
attacker from the open internet.

---

## Push to your own GitHub

This whole project is meant to live in your own public (or private) repo
so you can track changes and use the one-line installer above. From the
`romvault` folder:

```bash
git init
git add .
git commit -m "ROM Vault iteration 4"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/romvault.git
git push -u origin main
```

You'll need to create the (empty) repo on GitHub first — either via
the web UI (github.com → New repository → don't initialize with a
README/license/gitignore, since this folder already has its own), or
with the GitHub CLI if you have it installed:
```bash
gh repo create romvault --public --source=. --remote=origin --push
```

After that, edit `GITHUB_REPO` near the top of `proxmox-install.sh` (and
push that change) so the one-line installer points at your repo by
default instead of needing the env var every time.

**A couple of things NOT to commit** (already excluded via `.gitignore`,
just flagging why): the `instance/` folder holds your session secret key
and the SQLite database with everyone's password hashes, favorites, and
save states — none of that belongs in git history, even a private repo.

---

## Notes & known limitations

- **GC/Wii are download-only.** No usable browser emulator core exists
  for these yet — the PLAY button is intentionally disabled for them.
- **EmulatorJS loads from a public CDN** (`cdn.emulatorjs.org`) in the
  *player's browser*, not from the container — so the container itself
  needs no internet access, only your browser does, when you hit Play.
- The library list is cached for 5 minutes by default (configurable via
  the `CACHE_TTL_SECONDS` env var); hit **[ RESCAN ]** in the UI (or
  `POST /api/rescan`) any time to force an immediate refresh after adding
  new ROMs. Box art lookups are indexed per-system with a handful of
  directory listings rather than checked file-by-file, so this stays fast
  even at several thousand ROMs.
- The container mounts the NAS **read-only** by default — the app never
  writes to your ROM share. If you use the Skyscraper scraping tool, only
  the `boxart` subfolder gets a (separate, explicit) read-write mount —
  your ROM files stay protected either way.
- Multi-user accounts are suitable for a trusted home LAN (rate-limited
  login, hashed passwords, per-account data isolation). If you expose this
  beyond your LAN, put it behind a reverse proxy with TLS (e.g. Caddy or
  nginx with a Let's Encrypt cert) — the app itself doesn't do HTTPS.
- **Cross-device save-state auto-loading is the least-tested feature in
  this release** — see the caveat under "Accounts, favorites, recent, and
  save states" above.
- **`proxmox-install.sh` hasn't been run against a real Proxmox host** —
  same caveat as Skyscraper below: written carefully, not battle-tested.
  Try it on a throwaway VMID first.
- **Skyscraper hasn't been end-to-end tested against a real ROM set** in
  building this (no internet access in my build sandbox) — the flags and
  paths follow Skyscraper's documented CLI exactly, but if a particular
  flag behaves differently in practice, tell me what you see and I'll
  adjust `scrape_art.sh`.

## Ideas for iteration 5 (just say the word)

- "Continue playing" — surface last few played titles on the home screen
- Self-service password reset (currently admin-only via the users panel)
- Per-user access levels (e.g. a kid-safe view with a curated subset)
- Reverse proxy + HTTPS config included out of the box
- A scheduled/cron version of `scrape_art.sh` so new ROMs get art automatically
- Save-state screenshots (EmulatorJS provides one alongside each save;
  currently ignored — could show a thumbnail on hover)
