"""SQLite storage for lab pages. One file, WAL mode, versioned with PRAGMA user_version."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

SEED_DIR = Path(__file__).parent / "seed"

MIGRATIONS = [
    # v1
    """
    CREATE TABLE pages (
        id            TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        html          TEXT NOT NULL,
        technique     TEXT NOT NULL DEFAULT '',
        expected_label TEXT NOT NULL DEFAULT 'negative',
        notes         TEXT NOT NULL DEFAULT '',
        seed_key      TEXT UNIQUE,
        token_version INTEGER NOT NULL DEFAULT 1,
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL
    );
    """,
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_db_path(s: Settings) -> Path:
    if s.lab_db_path:
        return Path(s.lab_db_path)
    if vol := os.environ.get("RAILWAY_VOLUME_MOUNT_PATH"):
        return Path(vol) / "lab.db"
    if os.environ.get("RAILWAY_ENVIRONMENT") and not s.allow_ephemeral_db:
        raise RuntimeError(
            "Running on Railway without a volume: lab pages would be wiped on every redeploy. "
            "Attach a volume to this service (sets RAILWAY_VOLUME_MOUNT_PATH) or set ALLOW_EPHEMERAL_DB=1."
        )
    return Path("lab.db")


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5, isolation_level=None)  # autocommit; explicit BEGIN where needed
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.execute("BEGIN")
        for stmt in (x for x in sql.split(";") if x.strip()):
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version={i}")
        conn.execute("COMMIT")
    return len(MIGRATIONS)


def seed(conn: sqlite3.Connection) -> int:
    """Insert any seed pages that are missing (idempotent). Returns how many were added."""
    pages = json.loads((SEED_DIR / "pages.json").read_text())
    added = 0
    for p in pages:
        ts = now()
        cur = conn.execute(
            "INSERT OR IGNORE INTO pages (id, name, html, technique, expected_label, notes, seed_key, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex[:16], p["name"], p["html"], p["technique"], p["expected_label"], p["notes"], p["seed_key"], ts, ts),
        )
        added += cur.rowcount
    return added


def new_id() -> str:
    return uuid.uuid4().hex[:16]
