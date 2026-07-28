"""End-to-end tests that drive QueueCTL through its real CLI."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def test_list_json_contract(cli_env: dict[str, str]):
    run_cli(cli_env, "enqueue", json.dumps({"id": "json-1", "command": "echo ok"}))

    result = run_cli(cli_env, "list", "--state", "pending", "--json")

    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert payload[0]["id"] == "json-1"


def test_basic_job_completes(cli_env: dict[str, str]):
    run_cli(cli_env, "enqueue", json.dumps({"id": "basic-1", "command": "echo ok"}))

    worker = start_worker(cli_env)
    try:
        wait_for_job(cli_env, "completed", "basic-1")
        run_cli(cli_env, "worker", "stop")
        assert_worker_exits(worker)
    finally:
        stop_process(worker)


def test_failing_job_retries_then_enters_dlq(cli_env: dict[str, str]):
    run_cli(cli_env, "config", "set", "max-retries", "2")
    run_cli(cli_env, "config", "set", "backoff-base", "1")
    run_cli(cli_env, "config", "set", "poll-interval", "1")
    run_cli(cli_env, "enqueue", json.dumps({"id": "fail-1", "command": "exit 1"}))

    worker = start_worker(cli_env)
    try:
        dead = wait_for_job(cli_env, "dead", "fail-1", timeout=12)
        assert dead["attempts"] == 2
        run_cli(cli_env, "worker", "stop")
        assert_worker_exits(worker)
    finally:
        stop_process(worker)


def test_many_jobs_across_workers_execute_once(cli_env: dict[str, str], tmp_path: Path):
    recorder = tmp_path / "record_job.py"
    output = tmp_path / "executions.txt"
    recorder.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "path = Path(sys.argv[1])\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "with path.open('a', encoding='utf-8') as fh:\n"
        "    fh.write(sys.argv[2] + '\\n')\n",
        encoding="utf-8",
    )

    job_ids = [f"many-{index}" for index in range(12)]
    for job_id in job_ids:
        command = python_command(recorder, output, job_id)
        run_cli(cli_env, "enqueue", json.dumps({"id": job_id, "command": command}))

    worker = start_worker(cli_env, "--count", "3")
    try:
        for job_id in job_ids:
            wait_for_job(cli_env, "completed", job_id, timeout=15)
        run_cli(cli_env, "worker", "stop")
        assert_worker_exits(worker)
    finally:
        stop_process(worker)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert sorted(lines) == sorted(job_ids)
    assert len(lines) == len(set(lines)) == len(job_ids)


def test_sigkill_recovery_completes_without_duplicate_execution(
    cli_env: dict[str, str],
    tmp_path: Path,
):
    run_cli(cli_env, "config", "set", "poll-interval", "1")
    run_cli(cli_env, "config", "set", "heartbeat-interval", "1")
    run_cli(cli_env, "config", "set", "stale-threshold", "5")

    recorder = tmp_path / "slow_record.py"
    output = tmp_path / "crash_executions.txt"
    recorder.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "import time\n"
        "time.sleep(float(sys.argv[3]))\n"
        "path = Path(sys.argv[1])\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "with path.open('a', encoding='utf-8') as fh:\n"
        "    fh.write(sys.argv[2] + '\\n')\n",
        encoding="utf-8",
    )

    command = python_command(recorder, output, "crash-1", "2")
    run_cli(cli_env, "enqueue", json.dumps({"id": "crash-1", "command": command}))
    first_worker = start_worker(cli_env)
    wait_for_job(cli_env, "processing", "crash-1", timeout=5)
    first_worker.kill()
    first_worker.wait(timeout=5)

    second_worker = start_worker(cli_env)
    try:
        wait_for_job(cli_env, "completed", "crash-1", timeout=15)
        run_cli(cli_env, "worker", "stop")
        assert_worker_exits(second_worker)
    finally:
        stop_process(second_worker)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines == ["crash-1"]


def test_jobs_survive_full_restart(cli_env: dict[str, str]):
    run_cli(cli_env, "enqueue", json.dumps({"id": "restart-1", "command": "echo restart"}))

    pending = run_cli(cli_env, "list", "--state", "pending", "--json")
    assert json.loads(pending.stdout)[0]["id"] == "restart-1"

    worker = start_worker(cli_env)
    try:
        wait_for_job(cli_env, "completed", "restart-1")
        run_cli(cli_env, "worker", "stop")
        assert_worker_exits(worker)
    finally:
        stop_process(worker)


def run_cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "queuectl", *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def start_worker(env: dict[str, str], *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "queuectl", "worker", "start", *args],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_for_job(
    env: dict[str, str],
    state: str,
    job_id: str,
    timeout: float = 10,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run_cli(env, "list", "--state", state, "--json")
        for job in json.loads(result.stdout):
            if job["id"] == job_id:
                return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id!r} did not reach {state!r}")


def assert_worker_exits(worker: subprocess.Popen[str], timeout: float = 10) -> None:
    try:
        worker.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError("worker did not exit after stop request") from exc
    assert worker.returncode == 0, worker.stderr.read()


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def python_command(script: Path, *args: object) -> str:
    parts = [sys.executable, str(script), *(str(arg) for arg in args)]
    if os.name == "nt":
        return "& " + " ".join(_ps_quote(part) for part in parts)
    return " ".join(shlex.quote(part) for part in parts)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
