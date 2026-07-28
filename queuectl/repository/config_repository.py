"""Persisted runtime configuration."""

from __future__ import annotations

import sqlite3
from typing import Optional

DEFAULTS: dict[str, str] = {
    "max-retries": "3",
    "backoff-base": "2",
    "poll-interval": "2",
    "stale-threshold": "30",
    "heartbeat-interval": "5",
}


class ConfigRepository:
    """Read/write key-value configuration stored in SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def seed_defaults(self) -> None:
        for key, value in DEFAULTS.items():
            self._conn.execute(
                "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get(self, key: str, default: Optional[str] = None) -> str:
        row = self._conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        if row is None:
            if default is not None:
                return default
            if key in DEFAULTS:
                return DEFAULTS[key]
            raise KeyError(key)
        return str(row["value"])

    def get_int(self, key: str, default: Optional[int] = None) -> int:
        raw = self.get(key, default=None if default is None else str(default))
        return int(raw)

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def all(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key, value FROM config ORDER BY key").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

