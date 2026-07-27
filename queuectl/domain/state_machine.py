"""Application-level job state transition validator."""

from __future__ import annotations

from enum import Enum


class TransitionAction(str, Enum):
    ENQUEUE = "enqueue"
    CLAIM = "claim"
    COMPLETE = "complete"
    FAIL_RETRY = "fail_retry"
    FAIL_DEAD = "fail_dead"
    RECLAIM_ORPHAN = "reclaim_orphan"
    DLQ_RETRY = "dlq_retry"


_ALLOWED: dict[tuple[str, TransitionAction], str] = {
    ("pending", TransitionAction.CLAIM): "processing",
    ("failed", TransitionAction.CLAIM): "processing",
    ("processing", TransitionAction.COMPLETE): "completed",
    ("processing", TransitionAction.FAIL_RETRY): "failed",
    ("processing", TransitionAction.FAIL_DEAD): "dead",
    ("processing", TransitionAction.RECLAIM_ORPHAN): "pending",
    ("dead", TransitionAction.DLQ_RETRY): "pending",
}


class InvalidStateTransitionError(ValueError):
    """Raised when a job state transition violates the FSM."""


class StateTransitionValidator:
    """Enforces allowed job lifecycle transitions before persistence writes."""

    @staticmethod
    def target_state(current_state: str, action: TransitionAction) -> str:
        key = (current_state, action)
        if key not in _ALLOWED:
            raise InvalidStateTransitionError(
                f"transition {action.value!r} is not allowed from state {current_state!r}"
            )
        return _ALLOWED[key]

    @staticmethod
    def validate_enqueue() -> str:
        return "pending"

    @staticmethod
    def can_claim(state: str) -> bool:
        return state in {"pending", "failed"}

