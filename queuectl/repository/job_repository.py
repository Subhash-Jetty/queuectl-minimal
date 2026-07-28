"""All job persistence, atomic claiming, and lifecycle SQL."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from queuectl.domain.backoff import compute_retry_delay_seconds
from queuectl.domain.models import Job, utc_now_iso
from queuectl.domain.state_machine import StateTransitionValidator, TransitionAction
from queuectl.repository.config_repository import ConfigRepository
from queuectl.repository.worker_control_repository import WorkerControlRepository


class JobNotFoundError(LookupError):
    """Raised when a job id does not exist."""


class JobRepository:
    """Isolates every jobs-table query and transaction boundary."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._config = ConfigRepository(conn)
        self._workers = WorkerControlRepository(conn)
        self._validator = StateTransitionValidator()

    def enqueue(
        self,
        job_id: str,
        command: str,
        max_retries: Optional[int] = None,
    ) -> Job:
        now = utc_now_iso()
        retries = max_retries if max_retries is not None else self._config.get_int("max-retries")
        state = self._validator.validate_enqueue()
        self._conn.execute(
            """
            INSERT INTO jobs (
                id, command, state, attempts, max_retries, run_at,
                worker_id, last_error, exit_code, created_at, updated_at
            ) VALUES (?, ?, ?, 0, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            (job_id, command, state, retries, now, now, now),
        )
        job = self.get_by_id(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    def get_by_id(self, job_id: str) -> Optional[Job]:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def list_by_state(self, state: str) -> list[Job]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE state = ? ORDER BY created_at ASC",
            (state,),
        ).fetchall()
        return [Job.from_row(row) for row in rows]

    def status_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS count FROM jobs GROUP BY state"
        ).fetchall()
        counts = {str(row["state"]): int(row["count"]) for row in rows}
        for state in ("pending", "processing", "completed", "failed", "dead"):
            counts.setdefault(state, 0)
        return counts

    def claim_next_job(self, worker_id: str) -> Optional[Job]:
        """
        Atomically claim the oldest eligible job.

        Atomicity proof (DECISIONS.md #1):
          BEGIN IMMEDIATE acquires a RESERVED lock before any read/write.
          The single UPDATE ... RETURNING selects and transitions one row in one
          statement while the write lock is held. SQLite allows only one writer
          at a time across OS processes, so two workers cannot claim the same job.
        """
        now = utc_now_iso()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                """
                UPDATE jobs
                SET state = 'processing',
                    worker_id = ?,
                    attempts = attempts + 1,
                    updated_at = ?
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE state IN ('pending', 'failed')
                      AND datetime(run_at) <= datetime(?)
                    ORDER BY created_at ASC
                    LIMIT 1
                )
                RETURNING *
                """,
                (worker_id, now, now),
            ).fetchone()
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        if row is None:
            return None
        return Job.from_row(row)

    def mark_completed(self, job_id: str) -> Job:
        job = self._require(job_id)
        self._validator.target_state(job.state, TransitionAction.COMPLETE)
        now = utc_now_iso()
        self._conn.execute(
            """
            UPDATE jobs
            SET state = 'completed',
                worker_id = NULL,
                last_error = NULL,
                exit_code = 0,
                updated_at = ?
            WHERE id = ?
            """,
            (now, job_id),
        )
        return self._require(job_id)

    def mark_failed(self, job_id: str, exit_code: int, last_error: str) -> Job:
        job = self._require(job_id)
        now = utc_now_iso()
        if job.attempts >= job.max_retries:
            new_state = self._validator.target_state(job.state, TransitionAction.FAIL_DEAD)
            run_at = job.run_at
        else:
            new_state = self._validator.target_state(job.state, TransitionAction.FAIL_RETRY)
            base = self._config.get_int("backoff-base")
            delay = compute_retry_delay_seconds(base, job.attempts)
            run_at = _add_seconds_iso(now, delay)
        self._conn.execute(
            """
            UPDATE jobs
            SET state = ?,
                worker_id = NULL,
                last_error = ?,
                exit_code = ?,
                run_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (new_state, last_error, exit_code, run_at, now, job_id),
        )
        return self._require(job_id)

    def touch_processing_job(self, job_id: str) -> None:
        """Refresh updated_at for an in-flight job (crash-recovery heartbeat)."""
        now = utc_now_iso()
        self._conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ? AND state = 'processing'",
            (now, job_id),
        )

    def reclaim_stale_jobs(self, stale_seconds: int) -> int:
        """
        Move orphaned processing jobs back to pending.

        A job is orphaned when its updated_at is stale AND its owning worker
        heartbeat is also stale or missing (prevents reclaiming live work).
        """
        now = utc_now_iso()
        stale_mod = f"-{stale_seconds} seconds"
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self._conn.execute(
                """
                SELECT id, worker_id, state FROM jobs
                WHERE state = 'processing'
                  AND datetime(updated_at) <= datetime('now', ?)
                """,
                (stale_mod,),
            ).fetchall()

            reclaimed = 0
            for row in rows:
                worker_id = row["worker_id"]
                if worker_id and self._workers.is_worker_alive(worker_id, stale_seconds):
                    continue
                self._validator.target_state("processing", TransitionAction.RECLAIM_ORPHAN)
                self._conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'pending',
                        worker_id = NULL,
                        updated_at = ?
                    WHERE id = ? AND state = 'processing'
                    """,
                    (now, row["id"]),
                )
                reclaimed += 1
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return reclaimed

    def dlq_retry(self, job_id: str) -> Job:
        job = self._require(job_id)
        self._validator.target_state(job.state, TransitionAction.DLQ_RETRY)
        now = utc_now_iso()
        self._conn.execute(
            """
            UPDATE jobs
            SET state = 'pending',
                attempts = 0,
                run_at = ?,
                worker_id = NULL,
                last_error = NULL,
                exit_code = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, job_id),
        )
        return self._require(job_id)

    def _require(self, job_id: str) -> Job:
        job = self.get_by_id(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job


def _add_seconds_iso(iso_timestamp: str, seconds: int) -> str:
    normalized = iso_timestamp.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

