-- QueueCTL persistent schema (SQLite + WAL).
-- Applied idempotently on first connection via connection.py.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    command     TEXT NOT NULL,
    state       TEXT NOT NULL CHECK (state IN ('pending', 'processing', 'completed', 'failed', 'dead')),
    attempts    INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_retries INTEGER NOT NULL CHECK (max_retries >= 0),
    run_at      TEXT NOT NULL,
    worker_id   TEXT,
    last_error  TEXT,
    exit_code   INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_claim
    ON jobs (state, run_at, created_at);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_control (
    worker_id      TEXT PRIMARY KEY,
    pid            INTEGER NOT NULL,
    hostname       TEXT NOT NULL,
    stop_requested INTEGER NOT NULL DEFAULT 0 CHECK (stop_requested IN (0, 1)),
    last_heartbeat TEXT NOT NULL,
    registered_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_worker_control_heartbeat
    ON worker_control (last_heartbeat);

