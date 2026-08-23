"""
Runs Skyscraper against only the ROMs that don't have box art yet, in a
background thread, so it can be triggered automatically from [ RESCAN ]
instead of requiring a separate terminal session.

Unlike the original scripts/scrape_art.sh (which scans a whole system
folder and relies on Skyscraper's own --flags skipexistingcovers to avoid
redundant work), this builds a *filtered* staging folder containing only
symlinks to the specific ROMs missing art, so Skyscraper only ever touches
the actual gap -- faster repeat runs, and no reliance on Skyscraper's
internal cache behavior to honor "skip what's already matched."
"""
import os
import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path

SKYSCRAPER_BIN = os.environ.get("SKYSCRAPER_BIN", "/usr/local/bin/Skyscraper")
WORK_DIR = Path(os.environ.get("SKYSCRAPER_WORK_DIR", "/root/skyscraper-work"))
ARTWORK_XML = Path(os.environ.get("SKYSCRAPER_ARTWORK_XML", "/root/.skyscraper/romvault-artwork.xml"))
BOXART_EXTS = (".png", ".jpg", ".jpeg")

_state = {
    "running": False,
    "system": None,
    "done": 0,
    "total": 0,
    "error": None,
    "last_run": 0,
}
_lock = threading.Lock()


def skyscraper_available() -> bool:
    return os.path.isfile(SKYSCRAPER_BIN) and os.access(SKYSCRAPER_BIN, os.X_OK)


def status() -> dict:
    with _lock:
        return dict(_state)


def trigger(roms_root: str, boxart_root: str, missing_by_system: dict,
            screenscraper_user: str = "", screenscraper_pass: str = "") -> bool:
    """
    missing_by_system: {"nes": ["Metroid.nes", "Platformers/Mega Man 2.zip"], ...}
    Returns False if a scrape is already running (caller should just let
    that one finish rather than starting a second, overlapping one).
    """
    if not skyscraper_available():
        return False

    total = sum(len(v) for v in missing_by_system.values())
    if total == 0:
        return False

    with _lock:
        if _state["running"]:
            return False
        _state.update({"running": True, "error": None, "done": 0, "total": total, "system": None})

    threading.Thread(
        target=_run,
        args=(roms_root, boxart_root, missing_by_system, screenscraper_user, screenscraper_pass),
        daemon=True,
    ).start()
    return True


def _run(roms_root, boxart_root, missing_by_system, ss_user, ss_pass):
    try:
        for system, filenames in missing_by_system.items():
            if not filenames:
                continue
            with _lock:
                _state["system"] = system

            _scrape_one_system(roms_root, boxart_root, system, filenames, ss_user, ss_pass)

            with _lock:
                _state["done"] += len(filenames)
    except Exception as e:
        with _lock:
            _state["error"] = f"{e}\n{traceback.format_exc()}"
    finally:
        with _lock:
            _state["running"] = False
            _state["system"] = None
            _state["last_run"] = time.time()


def _scrape_one_system(roms_root, boxart_root, system, filenames, ss_user, ss_pass):
    real_system_dir = Path(roms_root) / system
    staging = WORK_DIR / system / "input"
    gamelist_dir = WORK_DIR / system / "gamelist"
    media_dir = WORK_DIR / system / "media"

    for d in (staging, gamelist_dir, media_dir):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)

    # Symlink only the missing ROMs into the staging folder, preserving
    # category subfolder structure, so Skyscraper's own recursive scan
    # only ever sees the gap -- not the whole library.
    linked_any = False
    for fname in filenames:
        src = real_system_dir / fname
        if not src.is_file():
            continue
        dst = staging / fname
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not dst.exists():
                os.symlink(src, dst)
                linked_any = True
        except OSError:
            continue

    if not linked_any:
        return

    cred_args = []
    if ss_user and ss_pass:
        cred_args = ["-u", f"{ss_user}:{ss_pass}"]

    subprocess.run(
        [SKYSCRAPER_BIN, "-p", system, "-s", "screenscraper",
         "-i", str(staging), *cred_args, "--flags", "unattend", "-t", "4"],
        check=False,
    )

    subprocess.run(
        [SKYSCRAPER_BIN, "-p", system,
         "-i", str(staging),
         "-g", str(gamelist_dir),
         "-o", str(media_dir),
         "-a", str(ARTWORK_XML),
         "--flags", "forcefilename,nobrackets,unattend,skipexistingcovers"],
        check=False,
    )

    dest = Path(boxart_root) / system
    dest.mkdir(parents=True, exist_ok=True)
    for img in media_dir.rglob("*"):
        if img.is_file() and img.suffix.lower() in BOXART_EXTS:
            try:
                shutil.copy2(img, dest / img.name)
            except OSError:
                continue

    shutil.rmtree(staging, ignore_errors=True)
