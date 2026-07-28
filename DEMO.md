# Tasko Manual Test Guide

The following commands can be used to manually verify the core functionality of the queue.

## 1. Clean Environment

**Terminal A:**
```powershell
$env:QUEUECTL_DB_PATH = "$PWD\.demo\jobs.db"
Remove-Item -Recurse -Force .demo -ErrorAction SilentlyContinue
```

## 2. Basic Job & Graceful Stop

**Terminal A:**
```powershell
python -m queuectl enqueue '{""id"":""job-basic"",""command"":""echo \""Job Completed Successfully\""""}'
python -m queuectl worker start --count 1
```

**Terminal B:**
```powershell
$env:QUEUECTL_DB_PATH = "$PWD\.demo\jobs.db"
python -m queuectl list --state completed --json
python -m queuectl worker stop
```
*Note: Terminal A will cleanly finish its current job and exit after receiving the stop signal from Terminal B.*

## 3. Retries and Dead Letter Queue (DLQ)

**Terminal A:**
```powershell
python -m queuectl config set max-retries 2
python -m queuectl config set backoff-base 1
python -m queuectl enqueue '{""id"":""job-fail"",""command"":""exit 1""}'
python -m queuectl worker start --count 1
```
*(Wait a few seconds for the job to retry twice and fail)*

**Terminal B:**
```powershell
python -m queuectl dlq list --json
python -m queuectl worker stop
```
*Note: The job transitions to the `dead` state after exhausting its retry budget.*

## 4. Concurrency (Multiple Workers)

**Terminal A:**
```powershell
python -m queuectl enqueue '{""id"":""multi-1"",""command"":""ping 127.0.0.1 -n 3""}'
python -m queuectl enqueue '{""id"":""multi-2"",""command"":""ping 127.0.0.1 -n 3""}'
python -m queuectl enqueue '{""id"":""multi-3"",""command"":""ping 127.0.0.1 -n 3""}'
python -m queuectl worker start --count 3
```

**Terminal B:**
```powershell
python -m queuectl status
python -m queuectl worker stop
```
*Note: Using `ping -n 3` simulates a longer running background task. The atomic locks ensure each worker gets exactly one job without overlap.*

## 5. Web Dashboard Synchronization

**Terminal A:**
```powershell
python -m queuectl dashboard --host 127.0.0.1 --port 8000
```

1. Open a browser to `http://127.0.0.1:8000`
2. In the enqueue form, type ID: `web-demo`, Command: `ping 127.0.0.1 -n 5`
3. Click **Enqueue**. Notice the `pending` count goes to 1.
4. **In Terminal B**, start a worker:
```powershell
python -m queuectl worker start --count 1
```
5. Watch the dashboard automatically move the job from `pending` -> `processing` -> `completed`.
6. Click the **Stop Workers** button in the dashboard to cleanly exit the worker in Terminal B.
