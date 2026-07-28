"""Cross-process worker registration and stop signaling."""

from __future__ import annotations

import sqlite3
from typing import Optional

from queuectl.domain.models import utc_now_iso


class WorkerControlRepository:
    """Workers register here; `worker stop` sets stop_requested across live rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def register(self, worker_id: str, pid: int, hostname: str) -> None:
        now = utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO worker_control (
                worker_id, pid, hostname, stop_requested, last_heartbeat, registered_at
            ) VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                pid = excluded.pid,
                hostname = excluded.hostname,
                stop_requested = 0,
                last_heartbeat = excluded.last_heartbeat,
                registered_at = excluded.registered_at
            """,
            (worker_id, pid, hostname, now, now),
        )

    def heartbeat(self, worker_id: str) -> bool:
        """Refresh heartbeat. Returns True if stop has been requested."""
        now = utc_now_iso()
        self._conn.execute(
            "UPDATE worker_control SET last_heartbeat = ? WHERE worker_id = ?",
            (now, worker_id),
        )
        row = self._conn.execute(
            "SELECT stop_requested FROM worker_control WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()
        return bool(row and row["stop_requested"])

    def request_stop_all(self, active_within_seconds: int = 60) -> int:
        """Mark all recently active workers for graceful shutdown."""
        cutoff = f"-{active_within_seconds} seconds"
        cursor = self._conn.execute(
            """
            UPDATE worker_control
            SET stop_requested = 1
            WHERE datetime(last_heartbeat) > datetime('now', ?)
            """,
            (cutoff,),
        )
        return cursor.rowcount

    def deregister(self, worker_id: str) -> None:
        self._conn.execute("DELETE FROM worker_control WHERE worker_id = ?", (worker_id,))

    def is_worker_alive(self, worker_id: str, stale_seconds: int) -> bool:
        row = self._conn.execute(
            """
            SELECT 1 FROM worker_control
            WHERE worker_id = ?
              AND datetime(last_heartbeat) > datetime('now', ?)
            """,
            (worker_id, f"-{stale_seconds} seconds"),
        ).fetchone()
        return row is not None

    def count_active(self, active_within_seconds: int = 60) -> int:
        cutoff = f"-{active_within_seconds} seconds"
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS count FROM worker_control
            WHERE datetime(last_heartbeat) > datetime('now', ?)
              AND stop_requested = 0
            """,
            (cutoff,),
        ).fetchone()
        return int(row["count"]) if row else 0

    def get(self, worker_id: str) -> Optional[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM worker_control WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()

