"""SQLite connection manager with WAL mode and schema bootstrap."""

from __future__ import annotations

import os
import sqlite3
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Optional

DEFAULT_DB_DIR = Path.home() / ".queuectl"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "jobs.db"
BUSY_TIMEOUT_MS = 5000


def get_db_path() -> Path:
    """Return the database file path (override in tests via QUEUECTL_DB_PATH)."""
    override = os.environ.get("QUEUECTL_DB_PATH")
    if override:
        return Path(override)
    return DEFAULT_DB_PATH


def open_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a fresh SQLite connection configured for QueueCTL."""
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


@lru_cache(maxsize=1)
def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Return a process-local SQLite connection.

    One connection per process avoids intra-process writer contention.
    Cross-process safety relies on SQLite's single-writer lock plus short transactions.
    """
    return open_connection(db_path)


def initialize_database(conn: Optional[sqlite3.Connection] = None) -> None:
    """Apply schema.sql and seed default configuration."""
    connection = conn or get_connection()
    schema_sql = resources.files("queuectl.db").joinpath("schema.sql").read_text(encoding="utf-8")
    connection.executescript(schema_sql)

    from queuectl.repository.config_repository import ConfigRepository

    ConfigRepository(connection).seed_defaults()


def reset_connection_cache() -> None:
    """Clear cached connection (used by tests)."""
    get_connection.cache_clear()

