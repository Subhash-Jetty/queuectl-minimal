"""Exponential backoff delay calculation."""

from __future__ import annotations


def compute_retry_delay_seconds(base: int, attempts: int) -> int:
    """
    Compute retry delay: delay = base ^ attempts (seconds).

    `attempts` is the number of completed attempts after the failed run
    (already incremented at claim time).
    """
    if base < 1:
        raise ValueError("backoff base must be >= 1")
    if attempts < 1:
        raise ValueError("attempts must be >= 1 for retry scheduling")
    return int(base**attempts)

