"""SQLite storage — single file, no external server required.

One database file holds three tables:

* ``chat_messages``  — per-user chat history (replaces Redis lists).
* ``counters``       — total / daily / per-user / per-category tallies.
* ``last_seen``      — when each user last wrote.

For our load (a private community chat) SQLite is more than fast enough,
and removes the need to run Redis locally or in a container.

If we ever outgrow it, the same SQL maps cleanly onto Postgres.
"""

import os
import sqlite3
from pathlib import Path


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id   TEXT    NOT NULL,
        message_json TEXT    NOT NULL,
        created_at   INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chat_messages_session
        ON chat_messages(session_id, id)
    """,
    """
    CREATE TABLE IF NOT EXISTS counters (
        key   TEXT    PRIMARY KEY,
        value INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS last_seen (
        user_id   TEXT    PRIMARY KEY,
        timestamp INTEGER NOT NULL
    )
    """,
]


def init_db(db_path: str) -> None:
    """Create tables if they don't exist. Safe to call repeatedly."""
    # An in-memory DB doesn't have a parent directory.
    if not db_path.startswith(":") and not db_path.startswith("file::"):
        parent = Path(db_path).resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        # Enable WAL — much better concurrent-read performance.
        if not db_path.startswith(":"):
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        for stmt in SCHEMA:
            conn.execute(stmt)
        conn.commit()


def connect(db_path: str) -> sqlite3.Connection:
    """Open a new connection. Caller is responsible for closing."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def healthcheck(db_path: str) -> None:
    """Run a trivial query to confirm the DB is reachable. Raises on failure."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("SELECT 1").fetchone()
