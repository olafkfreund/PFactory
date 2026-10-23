---
status: approved
issue: 905
intent: intent/2026-10-23-905-board-status-postgres.md
---

# Spec: board status in the shared Postgres store

## Design

Owner decision (2026-10-23): **persist the board-facing status fields to
Postgres**, next to the #904 task logs.

### Store

One table `task_board_states` (one Alembic migration revising `b3d9e1f7a5c2`, model
`TaskBoardState` in `server/database/models.py`):

- `task_id` (`"<project_id>:<spec_id>"`) primary key, `project_id` (indexed),
- `record` JSON: `{"task": <Task model as JSON, without repo>, "progress":
  <get_execution_progress result or null>, "archive": <archivedAt/archivedInVersion>}`,
  i.e. exactly what `spec_to_task` (via `load_spec_metadata`) and `task_to_dict`
  (via `get_execution_progress`) derive from the spec directory,
- `updated_at` (DB clock).

Queries live in `server/services/task_log_store.py` on the same
`async_session_factory` and the same `enabled()` gate (engine is not SQLite):
`save_board(task_id, project_id, record)` (upsert), `load_boards(project_ids)`
(one `WHERE project_id IN (...)` query), `load_board(task_id)`. `delete()` and
`prune()` also cover the new table.

### Write path

The pod that holds the spec directory writes the record whenever the
board-facing state changes:

- `AgentService._emit_progress` (every progress/phase event of a run, including
  the terminal one after `test_plan.json` is updated) schedules a snapshot;
- `PATCH /api/tasks/{id}/status` and `PATCH/PUT /api/tasks/{id}` schedule one
  after writing the plan or requirements.

`task_log_store.schedule_board(task_id, project_id, build)` coalesces: one flusher
per task, and `build()` (a sync call to a new `board_record(project_id, spec_dir)` in
`routes/tasks.py`) runs at flush time, so the last write always reflects the latest
files and two snapshots cannot commit out of order. A database error is logged and
dropped; the files are untouched.

### Read path (sync chain kept)

`load_spec_metadata` and `get_execution_progress` stay sync and file-only. The
readers do not become async; instead the routes, which are already async, load the
records once and pass them down:

- `GET /api/projects/{id}/tasks` and `GET /api/tasks`: tasks with a local spec
  directory are built from the files as today. If the store is enabled, one
  `load_boards` query fetches the records for the listed projects; each record
  whose task has no local spec directory becomes a `Task` from `record["task"]`.
  One query per request, whatever the task count.
- `task_to_dict(task, board=None)`: with a local spec directory it reads the files
  as today; otherwise it takes `executionProgress` and the archive fields from the
  record. `specsPath` stays null (the path belongs to the other pod).
- `GET /api/tasks/{id}`: when the spec directory is missing and a record exists,
  it returns the record instead of 404.

The file stays the source whenever the spec directory is on the serving pod, in
single-replica / SQLite mode always, and for in-flight tasks at deploy that have no
row yet.

Choice recorded: sync reads with a pre-loaded record map, not async readers. Making
`load_spec_metadata`/`spec_to_task` async would touch about ten call sites and
invite one query per task.

### Retention

`DELETE /api/tasks/{id}` already calls `task_log_store.delete`, which now also
deletes the board row. `PFACTORY_TASK_LOG_RETENTION_DAYS` (default 90) prunes board
rows by `updated_at`; documented in `docs/dev/environment-reference.md`.

## Alternatives rejected

- **Route board reads to the owning pod** (#805 ownership registry + pod-to-pod
  HTTP): the answer is lost when that pod restarts or is scaled in, and it adds an
  internal hop and its auth on every board load.
- **Async readers querying per task**: about ten call sites change and the task list
  costs one query per task.
- **Writing the record on every read on the owning pod**: one write per task per
  board load.

## Risks

- `projects.json` is still pod-local. A project registered on one pod only is not
  listed on the other; this change covers tasks of projects both pods know. Called
  out in the PR as a remaining gap for #273.
- Kanban edits made on the pod without the spec directory still fail as today.
- Write volume: one upsert per progress event per running task, coalesced.
- Additive migration; older pods ignore the table.

## Verification

`apps/web-server/tests/test_task_board_store.py` (SQLite engine, gate patched on):

- pod A (with spec dir) writes the record; pod B (empty project dir) returns the
  same status, phase, subtasks and `executionProgress` from
  `GET /api/projects/{id}/tasks`, `GET /api/tasks` and `GET /api/tasks/{id}`;
- with the store off, both lists are built from the files and nothing is written;
- the project task list issues the same number of queries for 1 and 5 remote tasks;
- `delete` removes the board row; `prune` removes old rows.

Plus `tests/postgres` alembic head test unchanged-green, ruff, `scripts/ratchet_lint.py`.
