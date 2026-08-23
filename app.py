import os
import re
import time
import secrets
import threading
import traceback
from pathlib import Path
from functools import wraps
from datetime import timedelta

from flask import (
    Flask, request, jsonify, render_template, send_file, abort,
    session, redirect, url_for, Response
)

import db
import scraper

app = Flask(__name__)
db.init_db()

# ---------------------------------------------------------------------------
# Config / paths -- resolved dynamically from admin settings (DB), falling
# back to env vars, falling back to hardcoded defaults. Read fresh each
# call (cheap local SQLite read) rather than cached at import time, so
# changing them in the admin Settings screen takes effect immediately,
# no service restart needed.
# ---------------------------------------------------------------------------
def roms_root() -> Path:
    configured = db.get_setting("roms_path")
    return Path(configured or os.environ.get("ROMS_PATH", "/mnt/roms")).resolve()


def boxart_root() -> Path:
    configured = db.get_setting("boxart_path")
    if configured:
        return Path(configured).resolve()
    env_val = os.environ.get("BOXART_PATH")
    if env_val:
        return Path(env_val).resolve()
    return (roms_root() / "boxart").resolve()


BOXART_EXTS = [".png", ".jpg", ".jpeg", ".webp"]

# Instance dir holds the app's own state: session secret key + the SQLite
# user/favorites/recent/savestate database. Must be writable by the
# service user (systemd unit grants this).
INSTANCE_DIR = Path(os.environ.get("INSTANCE_DIR", str(Path(__file__).parent / "instance")))
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
SECRET_KEY_FILE = INSTANCE_DIR / "secret_key"

# system key -> (display label, [extensions], emulatorjs core or None if unsupported in-browser)
# ".zip" is included everywhere: individually-zipped ROMs (one game per zip,
# the common distribution format) are common and EmulatorJS can usually
# load them directly. Downloading a zipped ROM always works regardless.
#
# "ejs_version" pins which EmulatorJS CDN build a system loads. Defaults to
# "stable" (see DEFAULT_EJS_VERSION below). NDS is pinned to 4.0.9 because
# 4.0.10+ shipped a regression that breaks touch/pointer input on the NDS
# touchscreen on mobile (confirmed on EmulatorJS's own official demo site --
# https://github.com/EmulatorJS/EmulatorJS/issues/814). Revisit this pin
# once that's fixed upstream.
DEFAULT_EJS_VERSION = "stable"

SYSTEMS = {
    "nes":  {"label": "Nintendo Entertainment System", "ext": [".nes", ".zip"],               "core": "nes"},
    "snes": {"label": "Super Nintendo",                 "ext": [".sfc", ".smc", ".zip"],       "core": "snes"},
    "n64":  {"label": "Nintendo 64",                     "ext": [".z64", ".n64", ".v64", ".zip"], "core": "n64"},
    "gba":  {"label": "Game Boy Advance",                "ext": [".gba", ".zip"],               "core": "gba"},
    "nds":  {"label": "Nintendo DS",                     "ext": [".nds", ".zip"],               "core": "nds", "ejs_version": "4.0.9"},
    "gc":   {"label": "GameCube",                        "ext": [".iso", ".rvz", ".gcm", ".zip"], "core": None},
    "wii":  {"label": "Wii",                             "ext": [".iso", ".rvz", ".wbfs", ".zip"], "core": None},
}

CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
# data/ts: last completed scan. scanning: a background scan is in flight.
# error: message from the last failed scan, if any (cleared on success).
_cache = {"data": [], "ts": 0, "scanning": False, "error": None}
_cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Secret key (persisted so sessions survive restarts)
# ---------------------------------------------------------------------------
if SECRET_KEY_FILE.exists():
    app.secret_key = SECRET_KEY_FILE.read_text().strip()
else:
    key = secrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key)
    try:
        os.chmod(SECRET_KEY_FILE, 0o600)
    except OSError:
        pass
    app.secret_key = key

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

# ---------------------------------------------------------------------------
# Very small in-memory login rate limiter (per-process; resets on restart).
# Good enough to slow down casual brute forcing on a home LAN; not a
# substitute for putting this behind a real reverse proxy if you expose it
# beyond your LAN.
# ---------------------------------------------------------------------------
_login_attempts = {}  # ip -> list[timestamps]
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300
LOCKOUT_SECONDS = 60


def _rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < WINDOW_SECONDS]
    _login_attempts[ip] = attempts
    if len(attempts) >= MAX_ATTEMPTS:
        return (now - attempts[-1]) < LOCKOUT_SECONDS
    return False


def _record_failed_attempt(ip: str):
    _login_attempts.setdefault(ip, []).append(time.time())


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if db.user_count() == 0:
            return redirect(url_for("setup"))
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def _enforce_setup_redirect():
    # If no account exists yet, force every non-setup request to the
    # setup screen (except static assets, which are harmless).
    if request.endpoint in ("static",):
        return
    if db.user_count() == 0 and request.endpoint not in ("setup",):
        return redirect(url_for("setup"))



# ---------------------------------------------------------------------------
# Library scanning
# ---------------------------------------------------------------------------
def _clean_title(filename: str) -> str:
    name = Path(filename).stem
    name = re.sub(r"[._]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _build_boxart_index(system: str):
    """
    Build a {rom-stem: art-url} lookup for one system with a handful of
    directory listings, instead of stat-checking dozens of candidate paths
    per ROM. This is what makes large libraries (thousands of files) scan
    in a reasonable time over a network share -- the old per-ROM approach
    did up to ~20 individual filesystem lookups *per ROM*.
    """
    index = {}
    system_art_dir = boxart_root() / system
    if not system_art_dir.is_dir():
        return index

    # Subfolders checked last-to-first so the flat/primary convention wins
    # on conflict (dict overwrite order below puts "" last == highest priority).
    search_dirs = [system_art_dir / sub for sub in ("box", "boxart", "cover", "covers")]
    search_dirs.append(system_art_dir)  # flat convention, highest priority

    for d in search_dirs:
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if not entry.is_file():
                        continue
                    stem, ext = os.path.splitext(entry.name)
                    if ext.lower() not in BOXART_EXTS:
                        continue
                    rel_dir = "" if d == system_art_dir else d.name
                    url = f"/boxart/{system}/{rel_dir}/{entry.name}" if rel_dir else f"/boxart/{system}/{entry.name}"
                    index[stem] = url
        except OSError:
            continue  # subfolder doesn't exist -- fine, just skip it

    return index


def _run_scan():
    """The actual (potentially slow, NAS-bound) scan. Runs in a background
    thread so it never blocks or times out an HTTP request/gunicorn worker,
    no matter how long a walk over a slow network share takes."""
    start = time.monotonic()
    library = []
    try:
        for system_key, meta in SYSTEMS.items():
            system_dir = roms_root() / system_key
            if not system_dir.is_dir():
                continue

            art_index = _build_boxart_index(system_key)
            exts = set(meta["ext"])

            for dirpath, _dirnames, filenames in os.walk(system_dir):
                for fname in filenames:
                    stem, ext = os.path.splitext(fname)
                    if ext.lower() not in exts:
                        continue
                    full_path = Path(dirpath) / fname
                    try:
                        size = full_path.stat().st_size
                    except OSError:
                        continue
                    rel = full_path.relative_to(system_dir)
                    category = str(rel.parent) if rel.parent != Path(".") else ""
                    library.append({
                        "system": system_key,
                        "system_label": meta["label"],
                        "playable": meta["core"] is not None,
                        "core": meta["core"],
                        "filename": str(rel),
                        "title": _clean_title(fname),
                        "category": category,
                        "size": size,
                        "art_url": art_index.get(stem),
                    })

        library.sort(key=lambda r: (r["system"], r["category"].lower(), r["title"].lower()))
        elapsed = time.monotonic() - start
        app.logger.info(f"Library scan: {len(library)} roms in {elapsed:.2f}s")
        with _cache_lock:
            _cache["data"] = library
            _cache["ts"] = time.time()
            _cache["error"] = None

        _maybe_trigger_scrape(library)
    except Exception as e:
        app.logger.error(f"Library scan failed: {e}\n{traceback.format_exc()}")
        with _cache_lock:
            _cache["error"] = str(e)
    finally:
        with _cache_lock:
            _cache["scanning"] = False


def _maybe_trigger_scrape(library):
    """After a scan completes, kick off an incremental Skyscraper scrape
    for anything still missing art -- only if Skyscraper is installed.
    Fully optional; silently does nothing if it's not set up."""
    if not scraper.skyscraper_available():
        return

    missing_by_system = {}
    for rom in library:
        if not rom["art_url"]:
            missing_by_system.setdefault(rom["system"], []).append(rom["filename"])

    if not missing_by_system:
        return

    ss_user = db.get_setting("screenscraper_user", "")
    ss_pass = db.get_setting("screenscraper_pass", "")
    started = scraper.trigger(
        str(roms_root()), str(boxart_root()), missing_by_system, ss_user, ss_pass
    )
    if started:
        total = sum(len(v) for v in missing_by_system.values())
        app.logger.info(f"Box art scrape started for {total} roms missing art")
        threading.Thread(target=_watch_scrape_then_rescan, daemon=True).start()


def _watch_scrape_then_rescan():
    """Newly-scraped art won't show up in art_url until another library
    scan runs (art_url is computed at walk time). Poll until scraping
    finishes, then trigger one more scan automatically so results appear
    without needing a second manual RESCAN click."""
    while scraper.status()["running"]:
        time.sleep(5)
    scan_library(force=True)


def scan_library(force: bool = False):
    """Returns whatever's currently cached immediately, kicking off a
    background rescan if the cache is stale/empty/forced and nothing is
    already in flight. Never blocks the caller on the actual filesystem
    walk -- that's what caused gunicorn worker timeouts on large NAS
    libraries before this was made asynchronous."""
    now = time.time()
    is_stale = force or not _cache["data"] or (now - _cache["ts"]) >= CACHE_TTL_SECONDS

    if is_stale:
        with _cache_lock:
            if not _cache["scanning"]:
                _cache["scanning"] = True
                threading.Thread(target=_run_scan, daemon=True).start()

    return _cache["data"]


def _resolve_within(base_dir: Path, filename: str) -> Path:
    base_dir = base_dir.resolve()
    candidate = (base_dir / filename).resolve()
    if base_dir not in candidate.parents and candidate != base_dir:
        abort(403)
    if not candidate.is_file():
        abort(404)
    return candidate


def _resolve_rom_path(system: str, filename: str) -> Path:
    if system not in SYSTEMS:
        abort(404)
    return _resolve_within(roms_root() / system, filename)


def _human_size(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
def _valid_username(username: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username or ""))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if db.user_count() > 0:
        # Setup already completed -- don't allow re-running it remotely.
        return redirect(url_for("login"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        if not _valid_username(username):
            error = "USERNAME MUST BE 3-32 CHARACTERS (LETTERS, NUMBERS, _ . -)"
        elif len(pw) < 8:
            error = "PASSWORD MUST BE AT LEAST 8 CHARACTERS"
        elif pw != pw2:
            error = "PASSWORDS DO NOT MATCH"
        else:
            user_id = db.create_user(username, pw, is_admin=True)
            session.permanent = True
            session["user_id"] = user_id
            session["username"] = username
            session["is_admin"] = True
            return redirect(url_for("index"))

    return render_template("login.html", mode="setup", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if db.user_count() == 0:
        return redirect(url_for("setup"))
    if session.get("user_id"):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        if _rate_limited(ip):
            error = "TOO MANY ATTEMPTS. WAIT 60 SECONDS AND RETRY."
        else:
            username = request.form.get("username", "")
            pw = request.form.get("password", "")
            user = db.verify_password(username, pw)
            if user:
                session.permanent = True
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["is_admin"] = bool(user["is_admin"])
                next_path = request.args.get("next") or url_for("index")
                return redirect(next_path)
            _record_failed_attempt(ip)
            error = "ACCESS DENIED — INCORRECT USERNAME OR PASSWORD"

    return render_template("login.html", mode="login", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/users", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users():
    error = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            username = request.form.get("username", "").strip()
            pw = request.form.get("password", "")
            is_admin = request.form.get("is_admin") == "on"
            if not _valid_username(username):
                error = "USERNAME MUST BE 3-32 CHARACTERS (LETTERS, NUMBERS, _ . -)"
            elif len(pw) < 8:
                error = "PASSWORD MUST BE AT LEAST 8 CHARACTERS"
            else:
                try:
                    db.create_user(username, pw, is_admin=is_admin)
                except db.UsernameTaken:
                    error = "USERNAME ALREADY TAKEN"
        elif action == "delete":
            target_id = int(request.form.get("user_id", 0))
            target = db.get_user_by_id(target_id)
            if target and target["id"] == session.get("user_id"):
                error = "CAN'T DELETE YOUR OWN ACCOUNT WHILE LOGGED IN AS IT"
            elif target and target["is_admin"] and db.admin_count() <= 1:
                error = "CAN'T DELETE THE LAST ADMIN ACCOUNT"
            elif target:
                db.delete_user(target_id)

    return render_template("admin_users.html", users=db.list_users(), error=error)


@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
@admin_required
def admin_settings():
    error = None
    saved = False

    if request.method == "POST":
        new_roms_path = request.form.get("roms_path", "").strip()
        new_boxart_path = request.form.get("boxart_path", "").strip()
        new_ss_user = request.form.get("screenscraper_user", "").strip()
        new_ss_pass = request.form.get("screenscraper_pass", "")

        if new_roms_path and not Path(new_roms_path).is_dir():
            error = f"ROMS PATH DOES NOT EXIST OR ISN'T A DIRECTORY: {new_roms_path}"
        elif new_boxart_path and not Path(new_boxart_path).parent.is_dir():
            error = f"BOXART PATH'S PARENT DOESN'T EXIST: {new_boxart_path}"
        else:
            db.set_setting("roms_path", new_roms_path)
            db.set_setting("boxart_path", new_boxart_path)
            db.set_setting("screenscraper_user", new_ss_user)
            # Only overwrite the stored password if a new one was actually
            # typed -- the form never echoes the real password back, so a
            # blank submit here should leave the existing one untouched.
            if new_ss_pass:
                db.set_setting("screenscraper_pass", new_ss_pass)
            scan_library(force=True)
            saved = True

    settings = {
        "roms_path": db.get_setting("roms_path", "") or str(roms_root()),
        "boxart_path": db.get_setting("boxart_path", "") or str(boxart_root()),
        "screenscraper_user": db.get_setting("screenscraper_user", ""),
        "has_screenscraper_pass": bool(db.get_setting("screenscraper_pass", "")),
        "skyscraper_available": scraper.skyscraper_available(),
    }
    return render_template("admin_settings.html", settings=settings, error=error, saved=saved)


# ---------------------------------------------------------------------------
# App routes
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def index():
    return render_template("index.html", systems=SYSTEMS, username=session.get("username"), is_admin=session.get("is_admin"))


@app.route("/api/systems")
@login_required
def api_systems():
    library = scan_library()
    counts = {key: 0 for key in SYSTEMS}
    for rom in library:
        counts[rom["system"]] += 1
    return jsonify([
        {"key": key, "label": meta["label"], "playable": meta["core"] is not None, "count": counts[key]}
        for key, meta in SYSTEMS.items()
    ])


@app.route("/api/roms")
@login_required
def api_roms():
    q = request.args.get("q", "").strip().lower()
    system = request.args.get("system", "").strip().lower()
    library = scan_library()

    results = library
    if system:
        results = [r for r in results if r["system"] == system]
    if q:
        results = [r for r in results if q in r["title"].lower() or q in r["category"].lower()]

    return jsonify([{**r, "size_human": _human_size(r["size"])} for r in results])


@app.route("/api/scan-status")
@login_required
def api_scan_status():
    return jsonify({
        "scanning": _cache["scanning"],
        "count": len(_cache["data"]),
        "last_scan": _cache["ts"],
        "error": _cache["error"],
    })


@app.route("/api/scrape-status")
@login_required
def api_scrape_status():
    s = scraper.status()
    s["available"] = scraper.skyscraper_available()
    return jsonify(s)


@app.route("/api/rescan", methods=["POST"])
@login_required
def api_rescan():
    scan_library(force=True)
    return jsonify({"status": "scanning"})


@app.route("/api/favorites")
@login_required
def api_favorites():
    return jsonify(db.get_favorites(session["user_id"]))


@app.route("/api/favorites/toggle", methods=["POST"])
@login_required
def api_favorites_toggle():
    body = request.get_json(silent=True) or {}
    system = body.get("system", "")
    filename = body.get("filename", "")
    if system not in SYSTEMS or not filename:
        abort(400)
    is_favorite = db.toggle_favorite(session["user_id"], system, filename)
    return jsonify({"favorite": is_favorite})


@app.route("/api/recent")
@login_required
def api_recent():
    return jsonify(db.get_recent(session["user_id"]))


@app.route("/api/savestate/<system>/<path:filename>", methods=["GET", "POST"])
@login_required
def api_savestate(system, filename):
    if system not in SYSTEMS:
        abort(404)
    if request.method == "POST":
        data = request.get_data()
        if not data:
            abort(400)
        db.put_savestate(session["user_id"], system, filename, data)
        return jsonify({"status": "saved", "bytes": len(data)})

    # GET: EmulatorJS's EJS_loadStateURL fetches this directly to auto-load
    # a save state at game start. No save yet -> 404, which EmulatorJS
    # should just treat as "start fresh" (an ordinary case for a new game).
    saved = db.get_savestate(session["user_id"], system, filename)
    if not saved:
        abort(404)
    return Response(saved["state"], mimetype="application/octet-stream")


@app.route("/download/<system>/<path:filename>")
@login_required
def download(system, filename):
    path = _resolve_rom_path(system, filename)
    return send_file(path, as_attachment=True, download_name=path.name)


@app.route("/rom/<system>/<path:filename>")
@login_required
def raw_rom(system, filename):
    path = _resolve_rom_path(system, filename)
    return send_file(path, as_attachment=False, conditional=True)


@app.route("/boxart/<system>/<path:filename>")
@login_required
def boxart(system, filename):
    if system not in SYSTEMS:
        abort(404)
    path = _resolve_within(boxart_root() / system, filename)
    return send_file(path, as_attachment=False, conditional=True)


@app.route("/play/<system>/<path:filename>")
@login_required
def play(system, filename):
    meta = SYSTEMS.get(system)
    if meta is None or meta["core"] is None:
        abort(404)
    path = _resolve_rom_path(system, filename)
    db.record_recent(session["user_id"], system, filename)
    return render_template(
        "play.html",
        system=system,
        core=meta["core"],
        title=_clean_title(path.name),
        filename=filename,
        rom_url=f"/rom/{system}/{filename}",
        savestate_url=f"/api/savestate/{system}/{filename}",
        ejs_version=meta.get("ejs_version", DEFAULT_EJS_VERSION),
    )


# Kick off an initial background scan as soon as the app loads, rather
# than waiting for the first request -- so by the time someone opens the
# page, indexing is already underway instead of starting from a cold,
# empty cache.
scan_library()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
