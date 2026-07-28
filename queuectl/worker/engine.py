"""Foreground worker loop, crash recovery, and graceful shutdown."""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import uuid
from dataclasses import dataclass

from queuectl.db.connection import get_db_path, initialize_database, open_connection
from queuectl.domain.models import Job
from queuectl.repository.config_repository import ConfigRepository
from queuectl.repository.job_repository import JobRepository
from queuectl.repository.worker_control_repository import WorkerControlRepository
from queuectl.worker.process import CommandResult, run_shell_command


@dataclass(frozen=True)
class WorkerSettings:
    poll_interval: int
    heartbeat_interval: int
    stale_threshold: int


def run_workers(count: int) -> int:
    """Run one or more workers in the foreground until stopped."""
    if count < 1:
        raise ValueError("--count must be >= 1")

    shutdown = threading.Event()
    _install_signal_handlers(shutdown)

    if count == 1:
        _run_worker_loop(1, shutdown)
        return 0

    threads = [
        threading.Thread(
            target=_run_worker_loop,
            args=(index, shutdown),
            name=f"queuectl-worker-{index}",
        )
        for index in range(1, count + 1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return 0


def _run_worker_loop(index: int, shutdown: threading.Event) -> None:
    conn = open_connection(get_db_path())
    initialize_database(conn)
    jobs = JobRepository(conn)
    workers = WorkerControlRepository(conn)
    config = ConfigRepository(conn)
    settings = _load_settings(config)
    worker_id = _worker_id(index)

    workers.register(worker_id, os.getpid(), socket.gethostname())
    _log(f"worker {worker_id} started")
    try:
        while True:
            stop_requested = _heartbeat(workers, worker_id)
            reclaimed = jobs.reclaim_stale_jobs(settings.stale_threshold)
            if reclaimed:
                _log(f"worker {worker_id} reclaimed {reclaimed} stale job(s)")

            if shutdown.is_set() or stop_requested:
                break

            job = jobs.claim_next_job(worker_id)
            if job is None:
                shutdown.wait(settings.poll_interval)
                continue

            _execute_job(jobs, workers, worker_id, job, settings)
    finally:
        workers.deregister(worker_id)
        conn.close()
        _log(f"worker {worker_id} stopped")


def _execute_job(
    jobs: JobRepository,
    workers: WorkerControlRepository,
    worker_id: str,
    job: Job,
    settings: WorkerSettings,
) -> None:
    _log(f"worker {worker_id} running job {job.id}")

    def heartbeat() -> None:
        _heartbeat(workers, worker_id)
        jobs.touch_processing_job(job.id)

    heartbeat()
    result = run_shell_command(job.command, heartbeat, settings.heartbeat_interval)
    heartbeat()
    if result.exit_code == 0:
        jobs.mark_completed(job.id)
        _log(f"worker {worker_id} completed job {job.id}")
        return

    failed = jobs.mark_failed(job.id, result.exit_code, _failure_text(result))
    _log(f"worker {worker_id} marked job {job.id} {failed.state}")


def _load_settings(config: ConfigRepository) -> WorkerSettings:
    return WorkerSettings(
        poll_interval=max(1, config.get_int("poll-interval")),
        heartbeat_interval=max(1, config.get_int("heartbeat-interval")),
        stale_threshold=max(5, config.get_int("stale-threshold")),
    )


def _install_signal_handlers(shutdown: threading.Event) -> None:
    def request_shutdown(signum: int, _frame: object) -> None:
        _log(f"received signal {signum}; finishing in-flight jobs before exit")
        shutdown.set()

    signal.signal(signal.SIGINT, request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_shutdown)


def _heartbeat(workers: WorkerControlRepository, worker_id: str) -> bool:
    return workers.heartbeat(worker_id)


def _worker_id(index: int) -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{index}-{uuid.uuid4().hex[:8]}"


def _failure_text(result: CommandResult) -> str:
    text = result.stderr.strip() or result.stdout.strip() or f"exit code {result.exit_code}"
    if len(text) > 4000:
        return text[:4000]
    return text


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)

