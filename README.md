# Tasko

Tasko is my implementation of the CLI-based persistent background job queue for the backend developer assignment. 

It uses the command name `queuectl` as requested by the assignment contract, and it handles everything required:
- Enqueueing shell commands as background jobs
- Running foreground worker processes that shut down gracefully
- Running multiple workers across different terminal sessions
- SQLite-backed persistence using WAL mode (so it handles concurrency well)
- Exponential backoff retries for failed jobs
- A Dead Letter Queue (DLQ) for jobs that fail too many times
- Crash recovery for when workers get killed (e.g. SIGKILL)

## Setup

It's built entirely with Python standard-library modules, so you don't need Redis or RabbitMQ or anything heavy. The only extra dependency is `pytest` for running the tests.

```powershell
# Clone and enter the directory
cd Tasko

# Set up a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install in editable mode with test dependencies
pip install -e ".[dev]"
```

## CLI Usage

Here's how to use it.

To enqueue a job (watch the JSON quoting depending on your shell):

**PowerShell:**
```powershell
python -m queuectl enqueue '{""id"":""job1"",""command"":""sleep 2""}'
```

**Bash:**
```bash
python3 -m queuectl enqueue '{"id":"job1","command":"sleep 2"}'
```

To start workers in the foreground (it blocks until stopped):
```powershell
python -m queuectl worker start --count 3
```

To gracefully stop workers from *another* terminal:
```powershell
python -m queuectl worker stop
```

To list pending jobs (this strictly outputs only a JSON array to stdout, as required):
```powershell
python -m queuectl list --state pending --json
```

Other useful commands:
```powershell
python -m queuectl status
python -m queuectl dlq list --json
python -m queuectl dlq retry job1
python -m queuectl config set max-retries 3
python -m queuectl config set backoff-base 2
python -m queuectl config list
```

## Bonus: Web Dashboard!

I also added a minimal web dashboard just for fun. It runs entirely on the standard library `http.server` and reads from the same SQLite database.

```powershell
python -m queuectl dashboard --host 127.0.0.1 --port 8000
```
Then open `http://127.0.0.1:8000` in your browser. You can see the queue status, enqueue jobs, and even request workers to stop.

## Architecture

- **Database:** `queuectl/db/connection.py` sets up SQLite in WAL mode with a busy timeout. By default it uses `~/.queuectl/jobs.db`, but tests override this using the `QUEUECTL_DB_PATH` environment variable.
- **Repository:** All SQL queries are isolated in `queuectl/repository/job_repository.py`. The atomic job claim uses a `BEGIN IMMEDIATE` transaction and an `UPDATE ... RETURNING` query so that multiple processes never claim the same job.
- **Workers:** `queuectl/worker/engine.py` handles the foreground loop. Workers register themselves, heartbeat while a command runs, and check for stale/crashed jobs on every iteration.
- **Execution:** `queuectl/worker/process.py` executes the actual shell commands. On Windows it uses PowerShell by default because it handles things like `sleep` better without needing extra binaries.

## Configuration Details

- `max-retries` is attached to a job when it gets enqueued, so changing it only affects new jobs.
- `backoff-base` is checked at the exact moment a job fails, so changing it affects the next retry calculation for already-enqueued jobs.
- Worker loop settings like `poll-interval` or `stale-threshold` are read when the worker starts, so you'll need to restart workers for those to take effect.

## Testing

Just run the test script:
```powershell
.\run_tests.cmd
```

The test suite covers all five mandatory scenarios from the assignment:
1. A basic job completes
2. A failing job retries with backoff and lands in the DLQ
3. Many jobs across multiple workers execute exactly once
4. A killed worker's job is recovered and executed (crash recovery)
5. Jobs survive a full restart

## Demo Recording

I have recorded a short demo showing the system in action:

`TODO: add link here`

If you want to follow along, you can use the `DEMO.md` script in the repo.
