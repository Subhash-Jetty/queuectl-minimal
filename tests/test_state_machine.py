"""Tests for the job state machine validator."""

import pytest

from queuectl.domain.state_machine import (
    InvalidStateTransitionError,
    StateTransitionValidator,
    TransitionAction,
)


def test_allowed_transitions():
    validator = StateTransitionValidator()
    assert validator.target_state("pending", TransitionAction.CLAIM) == "processing"
    assert validator.target_state("processing", TransitionAction.COMPLETE) == "completed"
    assert validator.target_state("processing", TransitionAction.FAIL_RETRY) == "failed"
    assert validator.target_state("processing", TransitionAction.FAIL_DEAD) == "dead"
    assert validator.target_state("processing", TransitionAction.RECLAIM_ORPHAN) == "pending"
    assert validator.target_state("dead", TransitionAction.DLQ_RETRY) == "pending"


def test_rejects_completed_to_processing():
    validator = StateTransitionValidator()
    with pytest.raises(InvalidStateTransitionError):
        validator.target_state("completed", TransitionAction.CLAIM)


def test_validate_enqueue():
    assert StateTransitionValidator.validate_enqueue() == "pending"
