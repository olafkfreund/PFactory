---
status: draft
issue: 829
intent: intent/2026-10-23-829-task-logs-postgres.md
---

# Spec: task logs in the shared Postgres store

## Design

Owner decision (2026-10-23): **option 2, move the per-task log record into
Postgres**, which every pod already shares.

### Store

Two tables in the web-server database, one Alembic migration revising the current
head `a8d3e6b2c9f4` (`apps/web-server/server/database/alembic/versions/`), models in
`server/database/models.py`. Both are keyed by the task id `"<project_id>:<spec_id>"`
the routes and `agent_service` already use.

- `task_log_phases`: `(task_id, phase)` primary key, `status`, `started_at`,
  `completed_at` (ISO strings, as in the file), `updated_at` (DB clock).
  One row per phase; a status change is one upsert, so no read-modify-write.
- `task_log_entries`: `id` autoincrement primary key, `task_id`, `phase`,
  `entry` (JSON, the same dict the file holds), `created_at` (DB clock).
  Index `(task_id, id)`. Append order is the `id` order, which the database
  assigns at commit, so concurrent writers cannot reorder each other's lines.

A new module `server/services/task_log_store.py` holds the queries
(`write`, `load`, `delete`, `prune`) on the existing `async_session_factory`
from `server/database/engine.py`. No new database client. "Shared store
configured" means the engine is not the SQLite default, i.e. `DATABASE_URL` points
at Postgres, the same signal #755/#757 and the #805 leases rely on.

### Write path

`TaskLogWriter` (`server/services/task_log_writer.py`) keeps writing
`task_logs.json` exactly as today. That file stays the owning pod's local copy for
the same-pod readers (`agent_failover`, `agent_worktree_sync`, the board status
readers) and the only record when no shared store is configured.

When it is given a `task_id` and the shared store is configured, it also queues each
entry and each phase-status change in order, and one flusher task per writer
(`server.background.tasks.spawn`) drains the queue into the two tables. The writer's
methods stay synchronous, so the call sites in `agent_process_monitor` do not change.
A database error is logged at ERROR and the batch is dropped. The task carries on and
the file still has the line.

`agent_service` passes `task_id` to the worktree writer only. The main-spec-dir
writer mirrors the same phase statuses, and writing both would duplicate rows.

The backend agent subprocess (`apps/backend/task_logger`) is not changed. It has no
database client and runs on the pod that spawned it. Its `__TASK_LOG_*__` stdout
markers already reach the web-server writer.

### Read path

`GET /api/tasks/{id}/logs` (`routes/tasks.py:get_task_logs`) asks the store first
when one is configured. If there are rows, it returns them in the shape it returns
today: `specId`, `createdAt`, `updatedAt`, and `phases` with the three default
phases filled in. If there are no rows (a task that ran before this shipped, or one
in flight at deploy time), it falls back to the file lookup unchanged. With no
shared store it reads the file as today.

The board status readers `load_spec_metadata` (~496) and `get_execution_progress`
(~830) stay on the file. Both start from a local `spec_dir` (`spec_dir.stat()`,
`test_plan.json`, `requirements.json`), so they only run on a pod that holds the
spec directory, and that pod holds the file too. Moving them would turn the sync
`spec_to_task` chain (about ten call sites) async and add a query per task on the
task list, and a pod without the spec directory would still be no better off.
**Remaining multi-replica gap, out of scope here:** the spec directory itself is
pod-local. That has to be solved before factory-gitops#273 lifts the pin.

### Retention

- `DELETE /api/tasks/{id}` deletes the task's rows with its spec directory.
- Age cap: `PFACTORY_TASK_LOG_RETENTION_DAYS` (default `90`; `0` disables). Rows
  older than the cap are deleted by `task_log_store.prune()`, which runs in the
  background each time a task run starts. If no tasks start, no rows are added, so
  nothing grows. The cap applies per row (entries by `created_at`, phases by
  `updated_at`). Documented in `docs/dev/environment-reference.md`.

## Alternatives rejected

- **RWX storage** (NFS / EFS / Azure Files / CephFS) for the data volume: no code
  change, but it is a storage-class requirement, slower, and not available
  everywhere. The NFS provisioner here already fails POSIX `mkdir` on the mount root
  (Factory#959).
- **Routing task-detail reads to the owning pod** (the #805 ownership registry plus
  pod-to-pod HTTP): the record is lost when that pod restarts or is scaled in, and
  it adds an internal HTTP hop and its auth.
- **One JSON document row per task** (the file as a blob): every line becomes a
  read-modify-write on one row. Concurrent writers would need row locks to keep
  ordering, and each append would rewrite the whole log.
- **Dropping the file when the store is configured**: it would break the same-pod
  file readers listed above for no gain.

## Risks

- Write volume: one INSERT per log line, batched by the flusher (text lines are
  already throttled to one entry per second). It is small next to the WS relay's
  NOTIFY traffic.
- The DB copy is incomplete if the database is down mid-run. A reader that finds
  some rows returns them and does not merge in the file. The ERROR log shows when
  this happens.
- A task re-run with the same spec id appends to the same rows, as the file does
  today.
- Migration is additive. Older pods ignore the tables, and a rollback leaves them
  unused.

## Verification

- `apps/web-server/tests/test_task_log_store.py` (SQLite, shared-store gate patched on):
  - a log written by writer "pod A" in directory A is returned by `get_task_logs`
    for a project whose path is an empty directory B;
  - two writers appending concurrently to one task keep each writer's order;
  - with the shared store off, nothing is written to the DB and the route reads
    the file;
  - with DB rows absent, the route falls back to the file;
  - `prune()` deletes rows older than the cap and keeps fresh ones; `delete_task`
    removes the rows.
- `tests/postgres/test_task_log_store_postgres.py`: the same write/read and prune
  against real Postgres after `alembic upgrade head` (CI `postgres-acceptance`).
- Backend and web-server suites, ruff, and `scripts/ratchet_lint.py`.
