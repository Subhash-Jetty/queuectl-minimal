"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from queuectl.db.connection import get_connection, initialize_database, reset_connection_cache


@pytest.fixture()
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("QUEUECTL_DB_PATH", str(db_path))
    reset_connection_cache()
    conn = get_connection(db_path)
    initialize_database(conn)
    yield conn
    conn.close()
    reset_connection_cache()
    if "QUEUECTL_DB_PATH" in os.environ:
        del os.environ["QUEUECTL_DB_PATH"]


@pytest.fixture()
def cli_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["QUEUECTL_DB_PATH"] = str(tmp_path / "cli.db")
    return env
