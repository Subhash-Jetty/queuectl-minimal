"""Tests for exponential backoff."""

import pytest

from queuectl.domain.backoff import compute_retry_delay_seconds


def test_backoff_base_two():
    assert compute_retry_delay_seconds(2, 1) == 2
    assert compute_retry_delay_seconds(2, 2) == 4
    assert compute_retry_delay_seconds(2, 3) == 8


def test_backoff_rejects_invalid_base():
    with pytest.raises(ValueError):
        compute_retry_delay_seconds(0, 1)


def test_backoff_rejects_invalid_attempts():
    with pytest.raises(ValueError):
        compute_retry_delay_seconds(2, 0)
