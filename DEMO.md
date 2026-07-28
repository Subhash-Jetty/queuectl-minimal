# QueueCTL Manual Demo Script

Use this script for the short recording or live screen-share.

## 1. Clean Demo Database

PowerShell:

```powershell
$env:QUEUECTL_DB_PATH = "$PWD\.demo\jobs.db"
Remove-Item -Recurse -Force .demo -ErrorAction SilentlyContinue
```

## 2. Enqueue And Complete A Job

Terminal A:

```powershell
python -m queuectl enqueue '{""id"":""demo-ok"",""command"":""echo hello""}'
python -m queuectl worker start --count 1
```

Terminal B:

```powershell
$env:QUEUECTL_DB_PATH = "$PWD\.demo\jobs.db"
python -m queuectl list --state completed --json
python -m queuectl worker stop
```

Point out that `worker start` ran in the foreground and stopped gracefully.

## 3. Retry And DLQ

```powershell
python -m queuectl config set max-retries 2
python -m queuectl config set backoff-base 1
python -m queuectl enqueue '{""id"":""demo-fail"",""command"":""exit 1""}'
python -m queuectl worker start --count 1
```

In another terminal:

```powershell
python -m queuectl dlq list --json
python -m queuectl worker stop
```

Point out that the job reached `dead` after its retry budget was exhausted.

## 4. Multiple Workers

```powershell
python -m queuectl enqueue '{""id"":""multi-1"",""command"":""sleep 1""}'
python -m queuectl enqueue '{""id"":""multi-2"",""command"":""sleep 1""}'
python -m queuectl enqueue '{""id"":""multi-3"",""command"":""sleep 1""}'
python -m queuectl worker start --count 3
```

Then:

```powershell
python -m queuectl status
python -m queuectl worker stop
```

## 5. Crash Recovery Talking Point

For the recorded demo, explain that the automated test `test_sigkill_recovery_completes_without_duplicate_execution` kills a worker mid-job, starts a new worker, and verifies the job completes once after stale-job recovery.

The exact recovery mechanism is documented in `DECISIONS.md`.

## 6. Optional Dashboard Bonus

```powershell
python -m queuectl dashboard --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, enqueue a small job from the form, and show that the dashboard updates counts and job lists from the same SQLite queue.
