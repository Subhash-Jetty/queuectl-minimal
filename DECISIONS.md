# Decisions

Answers to the five required questions from the assignment. I tried to be as specific as possible and point to exact code where it matters.

## 1. How two workers can't claim the same job

The key lines are in `queuectl/repository/job_repository.py`, inside `claim_next_job()`:

- `self._conn.execute("BEGIN IMMEDIATE")` — this grabs SQLite's RESERVED lock before anything else happens
- Then a single `UPDATE jobs ... WHERE id = (SELECT id FROM jobs WHERE state IN ('pending','failed') ... LIMIT 1) RETURNING *` finds the oldest eligible job and flips it to `processing` in one shot

The reason this works across separate OS processes is how SQLite's write locking works. `BEGIN IMMEDIATE` takes a RESERVED lock, and SQLite only allows one writer at a time — even across totally separate processes hitting the same file. So if two workers try to claim at the same moment, one of them blocks on `busy_timeout` until the first one commits. By the time the second worker's transaction runs, that job is already in `processing` and won't match the `WHERE` clause.

I specifically avoided doing a separate `SELECT` to find an eligible job and then an `UPDATE` to claim it, because that has a classic time-of-check/time-of-use race. Combining them into one statement inside the locked transaction eliminates that.

## 2. What happens when a worker gets SIGKILL'd

Let me walk through this step by step:

1. Worker A claims job X. The claim statement moves it to `state='processing'`, sets `worker_id` to A's id, and bumps `attempts` from 0 to 1. This is committed to the database.

2. Worker A starts running the shell command (like `sleep 10`). While it's running, the worker periodically heartbeats — updating both `worker_control.last_heartbeat` for itself and `jobs.updated_at` for job X. This happens every `heartbeat-interval` seconds (default 5).

3. Then SIGKILL hits. The process is gone instantly. No signal handler runs, no cleanup, nothing. The shell command's child process may or may not get killed depending on the OS, but the worker process is dead.

4. At this point in the database: job X is still `state='processing'` with `worker_id` pointing to dead worker A. Both the job's `updated_at` and worker A's `last_heartbeat` are frozen at whatever they were before the kill.

5. Meanwhile, worker B is running its normal loop. Every iteration, before trying to claim new work, it calls `reclaim_stale_jobs(stale_threshold)`. This method looks for jobs in `processing` where `updated_at` is older than `stale_threshold` seconds (default 30). But it doesn't just blindly reclaim — it also checks whether the owning worker's heartbeat is stale. This dual check prevents accidentally stealing a job from a worker that's just running a legitimately long command.

6. Since worker A is dead, both the job timestamp and the worker heartbeat are stale. So `reclaim_stale_jobs` moves job X back to `pending` (with `worker_id = NULL`). The `attempts` count stays at 1 — it doesn't get reset because the job did consume an attempt.

7. On the next poll cycle, worker B (or any worker) claims job X normally. `attempts` goes from 1 to 2, and the command runs from scratch.

Worst-case recovery delay is `stale_threshold` + `poll_interval` = 30 + 2 = **32 seconds**, which is comfortably under the 60-second requirement. In tests I use shorter values (5s threshold, 1s polling) to keep things fast.

## 3. Does `dlq retry` reset attempts?

Yes, it resets `attempts` to 0. It also clears `last_error` and `exit_code`, and sets the state back to `pending` with `run_at` set to now.

I think this is the right call because when someone manually retries a dead job, they've presumably investigated why it failed — maybe they fixed a misconfigured service, deployed a fix, or whatever. Giving the job a fresh retry budget makes `dlq retry` actually useful. If I kept the old attempt count, a job that died after 3 retries would immediately go back to `dead` on the first failure, which kind of defeats the purpose.

The tradeoff is that you lose the cumulative attempt count in the job row itself. If you needed audit history, you could add a `job_events` table, but the assignment doesn't require that so I kept the schema simple.

## 4. How `worker stop` works across processes

I use a `worker_control` table in the same SQLite database. When a worker starts, it registers a row with its `worker_id`, `pid`, `hostname`, and `last_heartbeat`. Every poll iteration, the worker calls `heartbeat()` which updates the timestamp and checks if `stop_requested` has been set to 1.

When you run `queuectl worker stop` from a different terminal, it sets `stop_requested = 1` for all workers whose heartbeat is recent enough to be considered active. The next time each worker heartbeats, it sees the flag and exits cleanly — finishing whatever job it's currently running first, then shutting down.

Alternatives I considered and rejected:

- **PID files**: These get messy with `--count 3` (which PID do you write?), and they go stale if a worker crashes without cleanup. You'd need to handle PID reuse too.
- **POSIX signals (like sending SIGTERM to a PID)**: This doesn't work well on Windows, and finding the right PID from a separate terminal is annoying. The whole point is that `worker stop` should be simple.
- **TCP socket / named pipe**: Would work, but it's a lot of extra moving parts for something that a simple database flag handles just fine.

The database approach reuses the same storage and locking we already have. The only downside is that it's cooperative — the worker has to check the flag. But that's actually fine because we need cooperative shutdown anyway (finish the current job before exiting).

## 5. Adding priority queues tomorrow

Most of the system stays the same:

- SQLite setup, WAL mode, connection management — no change
- Worker registration and stop signaling — no change  
- State machine transitions — no change (pending/processing/completed/failed/dead are the same)
- Retry logic, backoff, DLQ — no change
- CLI structure — just add an optional `--priority` flag to enqueue

What would need to change:

- Add a `priority INTEGER NOT NULL DEFAULT 0` column to the `jobs` table
- Update the index to something like `(state, run_at, priority DESC, created_at ASC)` so high-priority jobs get picked first
- Change the claim subquery's `ORDER BY` from `created_at ASC` to `priority DESC, created_at ASC`
- Update the enqueue JSON validation to accept an optional priority field
- Add tests that high-priority jobs jump ahead of lower-priority ones

The important thing is that priority only changes the ordering inside the claim statement. The transaction boundary, locking, and the whole worker loop don't need to change at all. That's the benefit of having the claim logic centralized in one repository method.
