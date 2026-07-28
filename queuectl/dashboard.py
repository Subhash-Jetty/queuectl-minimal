"""Minimal web dashboard for inspecting and operating QueueCTL."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from queuectl.db.connection import get_db_path, initialize_database, open_connection
from queuectl.repository.config_repository import ConfigRepository
from queuectl.repository.job_repository import JobRepository
from queuectl.repository.worker_control_repository import WorkerControlRepository

VALID_STATES = ("pending", "processing", "completed", "failed", "dead")


def serve_dashboard(host: str, port: int) -> int:
    """Start the dashboard HTTP server in the foreground."""
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"QueueCTL dashboard listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("dashboard stopped", flush=True)
    finally:
        server.server_close()
    return 0


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "QueueCTLDashboard/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(INDEX_HTML)
            return
        if parsed.path == "/api/status":
            self._send_json(_status_payload())
            return
        if parsed.path == "/api/jobs":
            query = parse_qs(parsed.query)
            state = query.get("state", ["pending"])[0]
            if state not in VALID_STATES:
                self._send_error(HTTPStatus.BAD_REQUEST, "invalid state")
                return
            self._send_json(_jobs_payload(state))
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/enqueue":
                self._send_json(_enqueue(payload), HTTPStatus.CREATED)
                return
            if parsed.path == "/api/dlq/retry":
                self._send_json(_dlq_retry(payload))
                return
            if parsed.path == "/api/worker/stop":
                self._send_json(_worker_stop())
                return
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)


def _repositories() -> tuple[JobRepository, WorkerControlRepository, ConfigRepository]:
    conn = open_connection(get_db_path())
    initialize_database(conn)
    return JobRepository(conn), WorkerControlRepository(conn), ConfigRepository(conn)


def _status_payload() -> dict[str, Any]:
    jobs, workers, config = _repositories()
    active_window = max(60, config.get_int("stale-threshold"))
    return {
        "counts": jobs.status_counts(),
        "active_workers": workers.count_active(active_window),
        "config": config.all(),
    }


def _jobs_payload(state: str) -> list[dict[str, Any]]:
    jobs, _workers, _config = _repositories()
    return [job.to_dict() for job in jobs.list_by_state(state)]


def _enqueue(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("id")
    command = payload.get("command")
    max_retries = payload.get("max_retries")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("id is required")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command is required")
    if max_retries is not None and (not isinstance(max_retries, int) or max_retries < 0):
        raise ValueError("max_retries must be a non-negative integer")

    jobs, _workers, _config = _repositories()
    return jobs.enqueue(job_id, command, max_retries=max_retries).to_dict()


def _dlq_retry(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("id is required")
    jobs, _workers, _config = _repositories()
    return jobs.dlq_retry(job_id).to_dict()


def _worker_stop() -> dict[str, int]:
    _jobs, workers, config = _repositories()
    active_window = max(60, config.get_int("stale-threshold"))
    return {"stopped": workers.request_stop_all(active_window)}


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QueueCTL Dashboard</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --line: #d8dee8;
      --text: #18202b;
      --muted: #647184;
      --accent: #176b87;
      --accent-strong: #0f4c5c;
      --danger: #b42318;
      --ok: #18794e;
      --warn: #9a6700;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      padding: 18px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 { margin: 0; font-size: 22px; font-weight: 650; }
    main { max-width: 1200px; margin: 0 auto; padding: 22px 18px 34px; }
    button, input, select {
      font: inherit;
      min-height: 36px;
      border-radius: 6px;
      border: 1px solid var(--line);
    }
    button {
      cursor: pointer;
      background: var(--panel);
      color: var(--text);
      padding: 0 12px;
    }
    button.primary { background: var(--accent); color: white; border-color: var(--accent); }
    button.danger { color: var(--danger); border-color: #efb5ad; }
    button:disabled { opacity: .55; cursor: wait; }
    input, select { padding: 0 10px; background: white; color: var(--text); }
    .toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .grid { display: grid; grid-template-columns: 1.1fr .9fr; gap: 18px; }
    .section { margin-bottom: 18px; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .cards { display: grid; grid-template-columns: repeat(6, minmax(110px, 1fr)); gap: 10px; }
    .metric { background: white; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
    .metric b { display: block; font-size: 24px; margin-bottom: 3px; }
    .metric span { color: var(--muted); font-size: 13px; }
    .tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
    .tabs button.active { background: #dceff4; border-color: #96c8d5; color: var(--accent-strong); }
    .jobs { display: grid; gap: 8px; }
    .job {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: start;
    }
    .job code { color: #263241; white-space: pre-wrap; overflow-wrap: anywhere; }
    .meta { color: var(--muted); font-size: 13px; margin-top: 6px; display: flex; gap: 12px; flex-wrap: wrap; }
    .state { font-size: 12px; font-weight: 700; text-transform: uppercase; }
    .state.completed { color: var(--ok); }
    .state.dead, .state.failed { color: var(--danger); }
    .state.processing { color: var(--warn); }
    form { display: grid; gap: 10px; }
    .form-row { display: grid; grid-template-columns: 1fr 2fr; gap: 10px; }
    .notice { min-height: 20px; color: var(--muted); font-size: 13px; }
    .error { color: var(--danger); }
    @media (max-width: 860px) {
      header { align-items: flex-start; flex-direction: column; }
      .grid, .form-row { grid-template-columns: 1fr; }
      .cards { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
      .job { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>QueueCTL Dashboard</h1>
    <div class="toolbar">
      <button id="refresh" title="Refresh dashboard">Refresh</button>
      <button id="stopWorkers" class="danger" title="Request graceful stop">Stop Workers</button>
    </div>
  </header>
  <main>
    <section class="section cards" id="metrics"></section>
    <div class="grid">
      <section class="panel">
        <div class="tabs" id="tabs"></div>
        <div class="jobs" id="jobs"></div>
      </section>
      <aside class="panel">
        <form id="enqueueForm">
          <div class="form-row">
            <input id="jobId" placeholder="job id" required>
            <input id="command" placeholder="command, e.g. sleep 2" required>
          </div>
          <button class="primary" type="submit">Enqueue</button>
          <div class="notice" id="notice"></div>
        </form>
      </aside>
    </div>
  </main>
  <script>
    const states = ["pending", "processing", "completed", "failed", "dead"];
    let selectedState = "pending";

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: {"Content-Type": "application/json"},
        ...options
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "request failed");
      return payload;
    }

    function setNotice(message, isError = false) {
      const node = document.querySelector("#notice");
      node.textContent = message;
      node.className = isError ? "notice error" : "notice";
    }

    function renderTabs() {
      document.querySelector("#tabs").innerHTML = states.map(state =>
        `<button class="${state === selectedState ? "active" : ""}" data-state="${state}">${state}</button>`
      ).join("");
    }

    function renderMetrics(status) {
      const counts = status.counts;
      document.querySelector("#metrics").innerHTML = [
        ...states.map(state => `<div class="metric"><b>${counts[state] || 0}</b><span>${state}</span></div>`),
        `<div class="metric"><b>${status.active_workers}</b><span>active workers</span></div>`
      ].join("");
    }

    function renderJobs(jobs) {
      const list = document.querySelector("#jobs");
      if (!jobs.length) {
        list.innerHTML = `<div class="notice">No ${selectedState} jobs.</div>`;
        return;
      }
      list.innerHTML = jobs.map(job => `
        <article class="job">
          <div>
            <div><strong>${job.id}</strong> <span class="state ${job.state}">${job.state}</span></div>
            <code>${job.command}</code>
            <div class="meta">
              <span>attempts ${job.attempts}/${job.max_retries}</span>
              <span>updated ${job.updated_at}</span>
              ${job.exit_code === undefined ? "" : `<span>exit ${job.exit_code}</span>`}
            </div>
          </div>
          ${job.state === "dead" ? `<button data-retry="${job.id}">Retry</button>` : ""}
        </article>
      `).join("");
    }

    async function refresh() {
      renderTabs();
      const [status, jobs] = await Promise.all([
        api("/api/status"),
        api(`/api/jobs?state=${selectedState}`)
      ]);
      renderMetrics(status);
      renderJobs(jobs);
    }

    document.querySelector("#tabs").addEventListener("click", async event => {
      if (!event.target.dataset.state) return;
      selectedState = event.target.dataset.state;
      await refresh();
    });

    document.querySelector("#jobs").addEventListener("click", async event => {
      const id = event.target.dataset.retry;
      if (!id) return;
      await api("/api/dlq/retry", {method: "POST", body: JSON.stringify({id})});
      setNotice(`requeued ${id}`);
      selectedState = "pending";
      await refresh();
    });

    document.querySelector("#enqueueForm").addEventListener("submit", async event => {
      event.preventDefault();
      const id = document.querySelector("#jobId").value.trim();
      const command = document.querySelector("#command").value.trim();
      try {
        await api("/api/enqueue", {method: "POST", body: JSON.stringify({id, command})});
        event.target.reset();
        setNotice(`enqueued ${id}`);
        selectedState = "pending";
        await refresh();
      } catch (error) {
        setNotice(error.message, true);
      }
    });

    document.querySelector("#refresh").addEventListener("click", refresh);
    document.querySelector("#stopWorkers").addEventListener("click", async () => {
      const result = await api("/api/worker/stop", {method: "POST", body: "{}"});
      setNotice(`stop requested for ${result.stopped} worker(s)`);
      await refresh();
    });

    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""

