"""
ROM Vault's data layer -- SQLite, single file, no external DB server.
Handles user accounts and everything that's now tied to an account instead
of a single shared password: favorites, recently-played, and cross-device
save states.
"""
import os
import time
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from werkzeug.security import generate_password_hash, check_password_hash

INSTANCE_DIR = Path(os.environ.get("INSTANCE_DIR", str(Path(__file__).parent / "instance")))
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = INSTANCE_DIR / "romvault.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    system TEXT NOT NULL,
    filename TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, system, filename)
);

CREATE TABLE IF NOT EXISTS recent (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    system TEXT NOT NULL,
    filename TEXT NOT NULL,
    played_at REAL NOT NULL,
    PRIMARY KEY (user_id, system, filename)
);

CREATE TABLE IF NOT EXISTS savestates (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    system TEXT NOT NULL,
    filename TEXT NOT NULL,
    state BLOB NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, system, filename)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS game_metadata (
    system TEXT NOT NULL,
    filename TEXT NOT NULL,
    title TEXT,
    description TEXT,
    developer TEXT,
    publisher TEXT,
    genre TEXT,
    players TEXT,
    release_date TEXT,
    rating REAL,
    screenshot_url TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (system, filename)
);
"""


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def user_count() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]


def admin_count() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) c FROM users WHERE is_admin = 1").fetchone()["c"]


class UsernameTaken(Exception):
    pass


def create_user(username: str, password: str, is_admin: bool = False) -> int:
    username = username.strip()
    with get_db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                (username, generate_password_hash(password), int(is_admin), time.time()),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise UsernameTaken(username)


def get_user_by_username(username: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, is_admin, created_at FROM users ORDER BY username COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_user(user_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def verify_password(username: str, password: str):
    user = get_user_by_username(username)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


def set_password(user_id: int, new_password: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------
def get_favorites(user_id: int):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT system, filename FROM favorites WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def toggle_favorite(user_id: int, system: str, filename: str) -> bool:
    """Returns the new state: True if now favorited, False if removed."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id=? AND system=? AND filename=?",
            (user_id, system, filename),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM favorites WHERE user_id=? AND system=? AND filename=?",
                (user_id, system, filename),
            )
            return False
        conn.execute(
            "INSERT INTO favorites (user_id, system, filename, created_at) VALUES (?, ?, ?, ?)",
            (user_id, system, filename, time.time()),
        )
        return True


# ---------------------------------------------------------------------------
# Recently played
# ---------------------------------------------------------------------------
def record_recent(user_id: int, system: str, filename: str, keep: int = 30):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO recent (user_id, system, filename, played_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, system, filename) DO UPDATE SET played_at = excluded.played_at",
            (user_id, system, filename, time.time()),
        )
        conn.execute(
            """
            DELETE FROM recent
            WHERE user_id = ?
              AND (system, filename) NOT IN (
                  SELECT system, filename FROM recent
                  WHERE user_id = ?
                  ORDER BY played_at DESC
                  LIMIT ?
              )
            """,
            (user_id, user_id, keep),
        )


def get_recent(user_id: int, limit: int = 30):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT system, filename, played_at FROM recent WHERE user_id = ? "
            "ORDER BY played_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Cross-device save states
# ---------------------------------------------------------------------------
def get_savestate(user_id: int, system: str, filename: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT state, updated_at FROM savestates WHERE user_id=? AND system=? AND filename=?",
            (user_id, system, filename),
        ).fetchone()
        return dict(row) if row else None


def put_savestate(user_id: int, system: str, filename: str, state_bytes: bytes):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO savestates (user_id, system, filename, state, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, system, filename) DO UPDATE SET "
            "state = excluded.state, updated_at = excluded.updated_at",
            (user_id, system, filename, state_bytes, time.time()),
        )


# ---------------------------------------------------------------------------
# Settings -- admin-configurable, override env-var defaults when set.
# Currently used for: roms_path, boxart_path, screenscraper_user,
# screenscraper_pass. Anything not set here falls back to env vars /
# hardcoded defaults, handled by the caller (see app.py's roms_root() etc).
# ---------------------------------------------------------------------------
def get_setting(key: str, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] not in (None, "") else default


def set_setting(key: str, value):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_all_settings() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# Game metadata -- scraped from Skyscraper's gamelist.xml (description,
# genre, developer, publisher, release date, rating, screenshot). Shared
# library data, not per-user, unlike favorites/recent/savestates above.
# ---------------------------------------------------------------------------
def upsert_game_metadata(system: str, filename: str, **fields):
    """fields: any of title, description, developer, publisher, genre,
    players, release_date, rating, screenshot_url."""
    allowed = {"title", "description", "developer", "publisher", "genre",
               "players", "release_date", "rating", "screenshot_url"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    columns = list(fields.keys())
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)
    update_clause = ", ".join(f"{c} = excluded.{c}" for c in columns)
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO game_metadata (system, filename, {col_list}, updated_at) "
            f"VALUES (?, ?, {placeholders}, ?) "
            f"ON CONFLICT(system, filename) DO UPDATE SET {update_clause}, updated_at = excluded.updated_at",
            (system, filename, *fields.values(), time.time()),
        )


def get_game_metadata(system: str, filename: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM game_metadata WHERE system = ? AND filename = ?",
            (system, filename),
        ).fetchone()
        return dict(row) if row else None


def get_titles_for_system(system: str) -> dict:
    """Bulk {filename: title} lookup for a whole system in one query --
    used to prefer the scraped title over the raw filename in list/grid
    views, without a DB call per ROM during a library scan."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT filename, title FROM game_metadata WHERE system = ? AND title IS NOT NULL",
            (system,),
        ).fetchall()
        return {r["filename"]: r["title"] for r in rows}
