"""Shared domain types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string with Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Job:
    id: str
    command: str
    state: str
    attempts: int
    max_retries: int
    run_at: str
    created_at: str
    updated_at: str
    worker_id: Optional[str] = None
    last_error: Optional[str] = None
    exit_code: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the assignment job JSON shape."""
        payload: dict[str, Any] = {
            "id": self.id,
            "command": self.command,
            "state": self.state,
            "attempts": self.attempts,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.worker_id is not None:
            payload["worker_id"] = self.worker_id
        if self.last_error is not None:
            payload["last_error"] = self.last_error
        if self.exit_code is not None:
            payload["exit_code"] = self.exit_code
        if self.run_at:
            payload["run_at"] = self.run_at
        return payload

    @classmethod
    def from_row(cls, row: Any) -> "Job":
        keys = row.keys() if hasattr(row, "keys") else row
        getter = row.__getitem__ if hasattr(row, "__getitem__") else (lambda k: getattr(row, k))
        return cls(
            id=getter("id"),
            command=getter("command"),
            state=getter("state"),
            attempts=int(getter("attempts")),
            max_retries=int(getter("max_retries")),
            run_at=getter("run_at"),
            worker_id=getter("worker_id"),
            last_error=getter("last_error"),
            exit_code=getter("exit_code"),
            created_at=getter("created_at"),
            updated_at=getter("updated_at"),
        )

