---
status: approved
issue: 829
author: olafkfreund
---

# Intent: task logs readable from any replica

## Problem

The web-server's `TaskLogWriter` (`apps/web-server/server/services/task_log_writer.py`)
records each task's per-phase execution log in `<spec_dir>/task_logs.json`, on the
filesystem of the pod that runs the task. `GET /api/tasks/{id}/logs`
(`apps/web-server/server/routes/tasks.py`) reads that file from whichever pod
serves the request. `pfactory-data` is `ReadWriteOnce` on `local-path`, so with two
replicas the other pod has no such file and the task detail view shows nothing.
#804 relays the live stream between pods, but the initial load and every refresh
still read the file.

## Proposed outcome

With the shared Postgres store configured, a task's phase log written on one pod is
returned by the logs endpoint on any pod, in the order it was written. Without a
shared store (single replica, local dev on SQLite) behaviour is unchanged. Tasks
that were already running when this ships keep showing their logs. Log rows do not
grow without bound.

## Affected users and systems

- web-server: `TaskLogWriter`, `agent_service` (writer setup), `routes/tasks.py`
  (logs endpoint, task delete), the SQLAlchemy models and Alembic migrations.
- Operators: one new env var for the retention cap.
- Not the backend agent subprocess (`apps/backend/task_logger`): it has no database
  client and runs on the same pod as the web-server that spawned it; what it logs
  reaches the web-server writer through its stdout markers.

## Constraints

- Reuse the existing async SQLAlchemy engine/session and Alembic; no new DB client.
- Do not lift the KEDA one-replica pin (factory-gitops#273) here; that follows
  after release.
- A database error while writing a log line must not break the running task.

## Open questions

None. The owner chose option 2 (Postgres) for the 2026-10-23 wave.
