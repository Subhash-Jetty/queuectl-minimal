"""Shell command execution for jobs."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


def run_shell_command(
    command: str,
    heartbeat: Callable[[], None],
    heartbeat_interval: int,
) -> CommandResult:
    """Run a job command through the platform shell while heartbeating."""
    process, cleanup = _start_process(command)
    stdout = ""
    stderr = ""
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=heartbeat_interval)
                break
            except subprocess.TimeoutExpired:
                heartbeat()
    finally:
        cleanup()

    return CommandResult(
        exit_code=int(process.returncode or 0),
        stdout=stdout or "",
        stderr=stderr or "",
    )


def _start_process(command: str) -> tuple[subprocess.Popen[str], Callable[[], None]]:
    popen_args: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }

    if os.name == "nt":
        shell_command = _windows_shell_command(command)
        process = subprocess.Popen(shell_command, **popen_args)
        return process, _assign_windows_kill_on_close_job(process)

    preexec_fn: Optional[Callable[[], None]] = None
    if threading.current_thread() is threading.main_thread():
        preexec_fn = _posix_child_setup
    process = subprocess.Popen(
        ["/bin/sh", "-c", command],
        preexec_fn=preexec_fn,
        start_new_session=preexec_fn is None,
        **popen_args,
    )
    return process, _noop


def _windows_shell_command(command: str) -> list[str]:
    explicit_shell = os.environ.get("QUEUECTL_SHELL")
    if explicit_shell:
        return _explicit_windows_shell_command(explicit_shell, command)

    powershell = shutil.which("powershell.exe")
    if powershell:
        return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
    return [os.environ.get("COMSPEC", "cmd.exe"), "/C", command]


def _explicit_windows_shell_command(shell: str, command: str) -> list[str]:
    shell_name = os.path.basename(shell).lower()
    if shell_name in {"cmd", "cmd.exe"}:
        return [shell, "/C", command]
    if shell_name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
    return [shell, "-lc", command]


def _posix_child_setup() -> None:
    os.setsid()
    try:
        import ctypes

        libc = ctypes.CDLL(None)
        libc.prctl(1, signal.SIGKILL)
    except Exception:
        pass


def _assign_windows_kill_on_close_job(process: subprocess.Popen[str]) -> Callable[[], None]:
    if os.name != "nt":
        return _noop

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return _noop

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        kernel32.SetInformationJobObject(
            job,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        kernel32.AssignProcessToJobObject(job, process._handle)

        def cleanup() -> None:
            kernel32.CloseHandle(job)

        return cleanup
    except Exception:
        return _noop


def _noop() -> None:
    return None

