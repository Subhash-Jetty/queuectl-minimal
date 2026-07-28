"""Tests for the optional web dashboard HTTP API."""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from queuectl.dashboard import DashboardHandler
from queuectl.db.connection import reset_connection_cache


def test_dashboard_homepage_and_api(cli_env: dict[str, str], monkeypatch):
    monkeypatch.setenv("QUEUECTL_DB_PATH", cli_env["QUEUECTL_DB_PATH"])
    reset_connection_cache()
    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        html = urllib.request.urlopen(f"{base_url}/", timeout=5).read().decode("utf-8")
        assert "QueueCTL Dashboard" in html

        created = post_json(
            f"{base_url}/api/enqueue",
            {"id": "dash-1", "command": "echo dashboard"},
        )
        assert created["id"] == "dash-1"
        assert created["state"] == "pending"

        pending = get_json(f"{base_url}/api/jobs?state=pending")
        assert pending[0]["id"] == "dash-1"

        status = get_json(f"{base_url}/api/status")
        assert status["counts"]["pending"] == 1
        assert "active_workers" in status

        stop = post_json(f"{base_url}/api/worker/stop", {})
        assert "stopped" in stop
    finally:
        server.shutdown()
        server.server_close()
        reset_connection_cache()
        os.environ.pop("QUEUECTL_DB_PATH", None)


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, object]):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
