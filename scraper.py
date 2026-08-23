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
import fcntl
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

import db

SKYSCRAPER_BIN = os.environ.get("SKYSCRAPER_BIN", "/usr/local/bin/Skyscraper")

# Both default to locations under /opt/romvault -- which the romvault
# service user actually owns -- rather than /root, which only root (the
# manual pct exec/enter workflow) can write to. Skyscraper itself also
# reads its own config/cache from $HOME/.skyscraper/, so subprocess calls
# below explicitly override HOME to match (see _run_skyscraper).
SKYSCRAPER_HOME = Path(os.environ.get("SKYSCRAPER_HOME", "/opt/romvault/skyscraper-home"))
WORK_DIR = Path(os.environ.get("SKYSCRAPER_WORK_DIR", "/opt/romvault/skyscraper-work"))
ARTWORK_XML = Path(os.environ.get(
    "SKYSCRAPER_ARTWORK_XML", str(SKYSCRAPER_HOME / ".skyscraper" / "romvault-artwork.xml")
))
BOXART_EXTS = (".png", ".jpg", ".jpeg")
# A large first-ever backlog scrape on a rate-limited ScreenScraper
# account can genuinely take hours for a single system's gather phase
# (observed: a ~3,700-file GBA batch still running, not hung, at the
# 30-minute mark with a 1-thread account limit) -- this is a safety net
# against a truly hung/frozen process, not a realistic expected duration.
# Override via SKYSCRAPER_TIMEOUT_SECONDS if even 4 hours isn't enough
# for a very large library.
SUBPROCESS_TIMEOUT_SECONDS = int(os.environ.get("SKYSCRAPER_TIMEOUT_SECONDS", "14400"))  # 4 hours

_state = {
    "running": False,
    "system": None,
    "done": 0,
    "total": 0,
    "error": None,
    "warnings": [],   # rolling list of per-system issues from the most recent run
    "last_run": 0,
}
_lock = threading.Lock()


def _log(msg: str):
    """Goes to stderr, which journald/systemctl captures the same way it
    already captures gunicorn's own output -- visible via
    `journalctl -u romvault` without any extra logging config."""
    print(f"[scraper] {msg}", file=sys.stderr, flush=True)


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
        _state.update({"running": True, "error": None, "warnings": [], "done": 0, "total": total, "system": None})

    threading.Thread(
        target=_run_with_process_lock,
        args=(roms_root, boxart_root, missing_by_system, screenscraper_user, screenscraper_pass),
        daemon=True,
    ).start()
    return True


def _run_with_process_lock(roms_root, boxart_root, missing_by_system, ss_user, ss_pass):
    """The in-process 'running' flag above only guards against a second
    call within the *same* Python process -- it can't see a scrape started
    by a different gunicorn worker process, since each worker has its own
    separate copy of that state entirely. An flock on a shared file is the
    actual cross-process guard: whichever process gets it first proceeds,
    any other process's non-blocking flock attempt fails immediately
    rather than racing the first one (which previously caused two workers
    to both scrape the same staging folder at once, corrupting each
    other's in-flight run)."""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = WORK_DIR / ".scrape.lock"
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _log("another process already holds the scrape lock -- skipping this trigger (expected, not an error, if multiple workers are running)")
        os.close(lock_fd)
        with _lock:
            _state["running"] = False
        return

    try:
        _run(roms_root, boxart_root, missing_by_system, ss_user, ss_pass)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def _run_skyscraper(args: list, system: str, phase: str):
    """Runs one Skyscraper invocation with output captured and logged.
    Never raises on a non-zero exit or a hang -- returns None on either,
    and records a diagnosable warning either way, so one bad system never
    kills the rest of a multi-system scrape run."""
    _log(f"{system} [{phase}]: running: {' '.join(args)}")
    env = {**os.environ, "HOME": str(SKYSCRAPER_HOME)}
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SECONDS, env=env
        )
    except subprocess.TimeoutExpired:
        msg = f"{system} [{phase}]: timed out after {SUBPROCESS_TIMEOUT_SECONDS}s"
        _log(msg)
        with _lock:
            _state["warnings"].append(msg)
        return None
    except OSError as e:
        msg = f"{system} [{phase}]: failed to launch Skyscraper: {e}"
        _log(msg)
        with _lock:
            _state["warnings"].append(msg)
        return None

    tail_out = (result.stdout or "").strip()[-800:]
    tail_err = (result.stderr or "").strip()[-800:]
    if result.returncode != 0:
        msg = f"{system} [{phase}]: Skyscraper exited {result.returncode}"
        if tail_err:
            msg += f" -- stderr: {tail_err}"
        elif tail_out:
            msg += f" -- output: {tail_out}"
        _log(msg)
        with _lock:
            _state["warnings"].append(msg)
    else:
        _log(f"{system} [{phase}]: exited 0" + (f" -- {tail_out[-200:]}" if tail_out else ""))

    return result


def _run(roms_root, boxart_root, missing_by_system, ss_user, ss_pass):
    try:
        for system, filenames in missing_by_system.items():
            if not filenames:
                continue
            with _lock:
                _state["system"] = system

            try:
                _scrape_one_system(roms_root, boxart_root, system, filenames, ss_user, ss_pass)
            except Exception as e:
                # A failure in one system (e.g. a read-only mount for that
                # system's art folder) must not abort every other
                # system's scrape too -- log it, record a warning, and
                # move on to the next system. Note: _scrape_one_system
                # increments "done" per-batch internally now, so whatever
                # batches completed before this exception are already
                # correctly counted -- nothing to add here.
                msg = f"{system}: unhandled error -- {e}"
                _log(f"{msg}\n{traceback.format_exc()}")
                with _lock:
                    _state["warnings"].append(msg)
    except Exception as e:
        # Catches anything outside the per-system try above (e.g. a bug
        # in iterating missing_by_system itself) -- should be rare, but
        # still shouldn't be allowed to leave "running" stuck True.
        with _lock:
            _state["error"] = f"{e}\n{traceback.format_exc()}"
    finally:
        with _lock:
            _state["running"] = False
            _state["system"] = None
            _state["last_run"] = time.time()


BATCH_SIZE = int(os.environ.get("SKYSCRAPER_BATCH_SIZE", "20"))


def _scrape_one_system(roms_root, boxart_root, system, filenames, ss_user, ss_pass):
    """Splits a system's missing ROMs into small batches and runs the full
    gather -> generate -> copy -> metadata cycle per batch, rather than one
    giant gather across potentially thousands of files. This means:
      - Art starts appearing after the first batch (~20 roms), not after
        the entire system finishes -- which could otherwise be hours away.
      - If something interrupts a run, only the current batch is lost,
        not the whole system's progress.
      - `done` advances smoothly batch by batch instead of jumping once
        at the very end."""
    batches = [filenames[i:i + BATCH_SIZE] for i in range(0, len(filenames), BATCH_SIZE)]
    _log(f"{system}: {len(filenames)} rom(s) split into {len(batches)} batch(es) of up to {BATCH_SIZE}")

    for batch_num, batch in enumerate(batches, 1):
        _log(f"{system}: starting batch {batch_num}/{len(batches)} ({len(batch)} rom(s))")
        try:
            _scrape_batch(roms_root, boxart_root, system, batch, ss_user, ss_pass)
        except Exception as e:
            # One batch's failure (e.g. a transient issue parsing that
            # batch's gamelist.xml) must not prevent the *rest* of this
            # system's batches from still being attempted -- that would
            # undermine the whole point of batching. A structural failure
            # (e.g. the destination folder is read-only) will naturally
            # keep failing every subsequent batch too and surface as a
            # repeated warning, which is still far better than silently
            # stopping partway with no indication why.
            msg = f"{system} batch {batch_num}/{len(batches)}: unhandled error -- {e}"
            _log(f"{msg}\n{traceback.format_exc()}")
            with _lock:
                _state["warnings"].append(msg)
        with _lock:
            _state["done"] += len(batch)


def _scrape_batch(roms_root, boxart_root, system, filenames, ss_user, ss_pass):
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
        msg = f"{system}: none of the {len(filenames)} missing rom(s) could be found/symlinked -- ROMs may have been moved or renamed since the last library scan"
        _log(msg)
        with _lock:
            _state["warnings"].append(msg)
        return

    cred_args = []
    if ss_user and ss_pass:
        cred_args = ["-u", f"{ss_user}:{ss_pass}"]

    _run_skyscraper(
        [SKYSCRAPER_BIN, "-p", system, "-s", "screenscraper",
         "-i", str(staging), *cred_args, "--flags", "unattend", "-t", "4"],
        system, "gather",
    )

    _run_skyscraper(
        [SKYSCRAPER_BIN, "-p", system,
         "-i", str(staging),
         "-g", str(gamelist_dir),
         "-o", str(media_dir),
         "-a", str(ARTWORK_XML),
         "--flags", "forcefilename,nobrackets,unattend,skipexistingcovers"],
        system, "generate",
    )

    dest = Path(boxart_root) / system
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for img in media_dir.rglob("*"):
        if img.is_file() and img.suffix.lower() in BOXART_EXTS:
            # Preserve the type subfolder (covers/, screenshots/) rather
            # than flattening everything into one directory -- cover and
            # screenshot share the same base filename (forcefilename), so
            # flattening them together would make one silently overwrite
            # the other.
            subfolder = img.parent.name
            target_dir = dest / subfolder
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(img, target_dir / img.name)
                copied += 1
            except OSError:
                continue

    if copied == 0:
        msg = f"{system}: ran against {len(filenames)} rom(s) but produced no art files -- check ScreenScraper credentials/rate limit, or that these titles actually exist in ScreenScraper's database"
        _log(msg)
        with _lock:
            _state["warnings"].append(msg)
    else:
        _log(f"{system}: {copied} art file(s) copied to {dest}")

    _parse_and_store_metadata(system, gamelist_dir, boxart_root)

    shutil.rmtree(staging, ignore_errors=True)


def _parse_and_store_metadata(system, gamelist_dir, boxart_root):
    """Skyscraper's generate phase already writes a gamelist.xml (standard
    EmulationStation format) alongside the media it produces -- this was
    previously just discarded. Parses it and stores description, genre,
    developer, publisher, release date, and rating per ROM, plus links up
    the screenshot copied above."""
    gamelist_path = gamelist_dir / "gamelist.xml"
    if not gamelist_path.is_file():
        return

    try:
        tree = ET.parse(gamelist_path)
    except ET.ParseError as e:
        msg = f"{system}: gamelist.xml failed to parse: {e}"
        _log(msg)
        with _lock:
            _state["warnings"].append(msg)
        return

    screenshots_dir = Path(boxart_root) / system / "screenshots"
    stored = 0

    for game in tree.getroot().findall("game"):
        path_el = game.find("path")
        if path_el is None or not path_el.text:
            continue
        filename = path_el.text
        if filename.startswith("./"):
            filename = filename[2:]

        def _text(tag):
            el = game.find(tag)
            return el.text.strip() if el is not None and el.text else None

        rating = None
        rating_raw = _text("rating")
        if rating_raw:
            try:
                rating = float(rating_raw)
            except ValueError:
                rating = None

        stem = Path(filename).stem
        screenshot_url = None
        for ext in BOXART_EXTS:
            candidate = screenshots_dir / f"{stem}{ext}"
            if candidate.is_file():
                screenshot_url = f"/boxart/{system}/screenshots/{stem}{ext}"
                break

        db.upsert_game_metadata(
            system, filename,
            title=_text("name"),
            description=_text("desc"),
            developer=_text("developer"),
            publisher=_text("publisher"),
            genre=_text("genre"),
            players=_text("players"),
            release_date=_text("releasedate"),
            rating=rating,
            screenshot_url=screenshot_url,
        )
        stored += 1

    if stored:
        _log(f"{system}: stored metadata for {stored} game(s) from gamelist.xml")
