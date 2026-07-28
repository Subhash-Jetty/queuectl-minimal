"""QueueCTL command-line interface."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any, Sequence

from queuectl.db.connection import get_connection, initialize_database
from queuectl.dashboard import serve_dashboard
from queuectl.repository.config_repository import ConfigRepository
from queuectl.repository.job_repository import JobNotFoundError, JobRepository
from queuectl.repository.worker_control_repository import WorkerControlRepository
from queuectl.worker.engine import run_workers

VALID_STATES = ("pending", "processing", "completed", "failed", "dead")
CONFIG_KEYS = {
    "max-retries": (0, None),
    "backoff-base": (1, None),
    "poll-interval": (1, None),
    "stale-threshold": (5, 59),
    "heartbeat-interval": (1, 30),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help(sys.stderr)
        return 2

    try:
        return int(args.handler(args))
    except (JobNotFoundError, ValueError, sqlite3.IntegrityError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="queuectl",
        description="CLI-based persistent background job queue.",
    )
    subcommands = parser.add_subparsers(dest="command")

    enqueue = subcommands.add_parser("enqueue", help="enqueue a job JSON object")
    enqueue.add_argument("job", help='JSON object such as {"id":"job1","command":"sleep 2"}')
    enqueue.set_defaults(handler=_cmd_enqueue)

    worker = subcommands.add_parser("worker", help="manage foreground workers")
    worker_subcommands = worker.add_subparsers(dest="worker_command", required=True)
    worker_start = worker_subcommands.add_parser("start", help="start workers in the foreground")
    worker_start.add_argument("--count", type=int, default=1, help="number of worker loops")
    worker_start.set_defaults(handler=_cmd_worker_start)
    worker_stop = worker_subcommands.add_parser("stop", help="request all active workers to stop")
    worker_stop.set_defaults(handler=_cmd_worker_stop)

    status = subcommands.add_parser("status", help="show queue state summary")
    status.set_defaults(handler=_cmd_status)

    list_jobs = subcommands.add_parser("list", help="list jobs by state")
    list_jobs.add_argument("--state", required=True, choices=VALID_STATES)
    list_jobs.add_argument("--json", action="store_true", help="print only a JSON array")
    list_jobs.set_defaults(handler=_cmd_list)

    dlq = subcommands.add_parser("dlq", help="dead-letter queue commands")
    dlq_subcommands = dlq.add_subparsers(dest="dlq_command", required=True)
    dlq_list = dlq_subcommands.add_parser("list", help="list dead jobs")
    dlq_list.add_argument("--json", action="store_true", help="print only a JSON array")
    dlq_list.set_defaults(handler=_cmd_dlq_list)
    dlq_retry = dlq_subcommands.add_parser("retry", help="re-enqueue a dead job")
    dlq_retry.add_argument("job_id")
    dlq_retry.set_defaults(handler=_cmd_dlq_retry)

    config = subcommands.add_parser("config", help="manage persisted configuration")
    config_subcommands = config.add_subparsers(dest="config_command", required=True)
    config_set = config_subcommands.add_parser("set", help="set a configuration value")
    config_set.add_argument("key", choices=sorted(CONFIG_KEYS))
    config_set.add_argument("value", type=int)
    config_set.set_defaults(handler=_cmd_config_set)
    config_list = config_subcommands.add_parser("list", help="list configuration")
    config_list.set_defaults(handler=_cmd_config_list)

    dashboard = subcommands.add_parser("dashboard", help="start optional web dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8000)
    dashboard.set_defaults(handler=_cmd_dashboard)

    return parser


def _repositories() -> tuple[JobRepository, WorkerControlRepository, ConfigRepository]:
    conn = get_connection()
    initialize_database(conn)
    return JobRepository(conn), WorkerControlRepository(conn), ConfigRepository(conn)


def _cmd_enqueue(args: argparse.Namespace) -> int:
    payload = _parse_job_payload(args.job)
    jobs, _workers, _config = _repositories()
    job = jobs.enqueue(
        payload["id"],
        payload["command"],
        max_retries=payload.get("max_retries"),
    )
    print(json.dumps(job.to_dict(), sort_keys=True))
    return 0


def _cmd_worker_start(args: argparse.Namespace) -> int:
    conn = get_connection()
    initialize_database(conn)
    return run_workers(args.count)


def _cmd_worker_stop(_args: argparse.Namespace) -> int:
    _jobs, workers, config = _repositories()
    active_window = max(60, config.get_int("stale-threshold"))
    count = workers.request_stop_all(active_within_seconds=active_window)
    print(f"stop requested for {count} worker(s)")
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    jobs, workers, config = _repositories()
    counts = jobs.status_counts()
    active_window = max(60, config.get_int("stale-threshold"))
    active_workers = workers.count_active(active_within_seconds=active_window)
    for state in VALID_STATES:
        print(f"{state}: {counts[state]}")
    print(f"active_workers: {active_workers}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    jobs, _workers, _config = _repositories()
    rows = jobs.list_by_state(args.state)
    _print_jobs(rows, as_json=args.json)
    return 0


def _cmd_dlq_list(args: argparse.Namespace) -> int:
    jobs, _workers, _config = _repositories()
    _print_jobs(jobs.list_by_state("dead"), as_json=args.json)
    return 0


def _cmd_dlq_retry(args: argparse.Namespace) -> int:
    jobs, _workers, _config = _repositories()
    job = jobs.dlq_retry(args.job_id)
    print(json.dumps(job.to_dict(), sort_keys=True))
    return 0


def _cmd_config_set(args: argparse.Namespace) -> int:
    _jobs, _workers, config = _repositories()
    value = _validate_config_value(args.key, args.value)
    config.set(args.key, str(value))
    print(f"{args.key}={value}")
    return 0


def _cmd_config_list(_args: argparse.Namespace) -> int:
    _jobs, _workers, config = _repositories()
    for key, value in config.all().items():
        print(f"{key}={value}")
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    if args.port < 1 or args.port > 65535:
        raise ValueError("port must be between 1 and 65535")
    conn = get_connection()
    initialize_database(conn)
    return serve_dashboard(args.host, args.port)


def _parse_job_payload(raw: str) -> dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("enqueue payload must be a JSON object")
    for key in ("id", "command"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f"enqueue payload requires non-empty string {key!r}")
    if "max_retries" in payload:
        retries = payload["max_retries"]
        if not isinstance(retries, int) or retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
    return payload


def _validate_config_value(key: str, value: int) -> int:
    minimum, maximum = CONFIG_KEYS[key]
    if value < minimum:
        raise ValueError(f"{key} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{key} must be <= {maximum}")
    return value


def _print_jobs(rows: list[Any], as_json: bool) -> None:
    if as_json:
        json.dump([job.to_dict() for job in rows], sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        return
    for job in rows:
        print(f"{job.id}\t{job.state}\tattempts={job.attempts}\t{job.command}")


if __name__ == "__main__":
    raise SystemExit(main())

