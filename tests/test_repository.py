"""Tests for JobRepository atomic operations and lifecycle."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from queuectl.domain.models import utc_now_iso
from queuectl.repository.config_repository import ConfigRepository
from queuectl.repository.job_repository import JobRepository
from queuectl.repository.worker_control_repository import WorkerControlRepository


def _repo(conn: sqlite3.Connection) -> JobRepository:
    return JobRepository(conn)


def test_enqueue_and_get(temp_db: sqlite3.Connection):
    repo = _repo(temp_db)
    job = repo.enqueue("job-1", "echo hello", max_retries=3)
    assert job.id == "job-1"
    assert job.state == "pending"
    assert job.attempts == 0
    assert repo.get_by_id("job-1") is not None


def test_claim_moves_job_to_processing(temp_db: sqlite3.Connection):
    repo = _repo(temp_db)
    repo.enqueue("job-1", "echo hello")
    claimed = repo.claim_next_job("worker-a")
    assert claimed is not None
    assert claimed.id == "job-1"
    assert claimed.state == "processing"
    assert claimed.attempts == 1
    assert claimed.worker_id == "worker-a"


def test_claim_returns_none_when_empty(temp_db: sqlite3.Connection):
    repo = _repo(temp_db)
    assert repo.claim_next_job("worker-a") is None


def test_claim_fifo_order(temp_db: sqlite3.Connection):
    repo = _repo(temp_db)
    repo.enqueue("older", "echo old")
    repo.enqueue("newer", "echo new")
    claimed = repo.claim_next_job("worker-a")
    assert claimed is not None
    assert claimed.id == "older"


def test_concurrent_claims_only_one_winner(temp_db: sqlite3.Connection, tmp_path):
    """Separate connections per thread mirror separate OS worker processes."""
    from queuectl.db.connection import get_connection, reset_connection_cache

    db_path = tmp_path / "concurrent.db"
    import os

    os.environ["QUEUECTL_DB_PATH"] = str(db_path)
    reset_connection_cache()
    main_conn = get_connection(db_path)
    from queuectl.db.connection import initialize_database

    initialize_database(main_conn)
    JobRepository(main_conn).enqueue("only-one", "echo x")

    results: list[str | None] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        try:
            conn = sqlite3.connect(str(db_path), timeout=5, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            job = JobRepository(conn).claim_next_job(name)
            with lock:
                results.append(job.id if job else None)
            conn.close()
        except Exception as exc:  # pragma: no cover - failure path
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert results.count("only-one") == 1
    assert sum(1 for item in results if item is not None) == 1
    main_conn.close()
    reset_connection_cache()


def test_mark_completed(temp_db: sqlite3.Connection):
    repo = _repo(temp_db)
    repo.enqueue("job-1", "echo ok")
    repo.claim_next_job("worker-a")
    done = repo.mark_completed("job-1")
    assert done.state == "completed"
    assert done.exit_code == 0


def test_mark_failed_schedules_retry(temp_db: sqlite3.Connection):
    repo = _repo(temp_db)
    repo.enqueue("job-1", "exit 1", max_retries=3)
    repo.claim_next_job("worker-a")
    failed = repo.mark_failed("job-1", 1, "boom")
    assert failed.state == "failed"
    assert failed.attempts == 1
    assert repo.claim_next_job("worker-b") is None


def test_mark_failed_moves_to_dead_after_max_retries(temp_db: sqlite3.Connection):
    repo = _repo(temp_db)
    repo.enqueue("job-1", "exit 1", max_retries=1)
    repo.claim_next_job("worker-a")
    dead = repo.mark_failed("job-1", 1, "boom")
    assert dead.state == "dead"
    assert dead.attempts == 1


def test_dlq_retry_resets_attempts(temp_db: sqlite3.Connection):
    repo = _repo(temp_db)
    repo.enqueue("job-1", "exit 1", max_retries=1)
    repo.claim_next_job("worker-a")
    repo.mark_failed("job-1", 1, "boom")
    retried = repo.dlq_retry("job-1")
    assert retried.state == "pending"
    assert retried.attempts == 0


def test_reclaim_stale_job_when_worker_dead(temp_db: sqlite3.Connection):
    jobs = _repo(temp_db)
    workers = WorkerControlRepository(temp_db)
    jobs.enqueue("job-1", "sleep 30")
    claimed = jobs.claim_next_job("worker-dead")
    assert claimed is not None

    stale = (datetime.now(timezone.utc) - timedelta(minutes=2)).replace(microsecond=0)
    stale_iso = stale.isoformat().replace("+00:00", "Z")
    temp_db.execute("UPDATE jobs SET updated_at = ? WHERE id = 'job-1'", (stale_iso,))
    workers.register("worker-dead", 99999, "test-host")
    temp_db.execute(
        "UPDATE worker_control SET last_heartbeat = ? WHERE worker_id = 'worker-dead'",
        (stale_iso,),
    )

    reclaimed = jobs.reclaim_stale_jobs(30)
    assert reclaimed == 1
    pending = jobs.list_by_state("pending")
    assert len(pending) == 1
    assert pending[0].id == "job-1"


def test_reclaim_does_not_touch_live_worker_job(temp_db: sqlite3.Connection):
    jobs = _repo(temp_db)
    workers = WorkerControlRepository(temp_db)
    jobs.enqueue("job-1", "sleep 30")
    jobs.claim_next_job("worker-live")
    workers.register("worker-live", 12345, "test-host")
    workers.heartbeat("worker-live")
    jobs.touch_processing_job("job-1")

    reclaimed = jobs.reclaim_stale_jobs(30)
    assert reclaimed == 0
    assert jobs.list_by_state("processing")[0].id == "job-1"


def test_status_counts(temp_db: sqlite3.Connection):
    repo = _repo(temp_db)
    repo.enqueue("a", "echo a")
    repo.enqueue("b", "echo b")
    repo.claim_next_job("w1")
    counts = repo.status_counts()
    assert counts["pending"] == 1
    assert counts["processing"] == 1


def test_config_seeded(temp_db: sqlite3.Connection):
    config = ConfigRepository(temp_db)
    assert config.get_int("max-retries") == 3
    assert config.get_int("backoff-base") == 2
