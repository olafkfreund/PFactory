---
status: draft
issue: 905
author: olafkfreund
---

# Intent: board status readable from any replica

## Problem

The board's status readers in `apps/web-server/server/routes/tasks.py`,
`load_spec_metadata` and `get_execution_progress`, build a task's status, active
phase, subtasks and execution progress from files in the task's spec directory.
That directory lives only on the pod that ran the task. With two replicas, a board
(`GET /api/projects/{id}/tasks`), task-list (`GET /api/tasks`) or task-detail
(`GET /api/tasks/{id}`) request served by the other pod has no spec directory, so it
shows the task without its phase or progress. #829 (PR #904) moved the logs to
Postgres; this is the last gap before factory-gitops#273 lifts the one-replica pin.

## Proposed outcome

With the shared Postgres store configured, the board, task list and task detail
show the same status, phase, subtasks and progress from either replica. Without a
shared store (single replica, SQLite) behaviour is unchanged. Tasks in flight at
deploy time keep showing from their files on the owning pod.

## Affected users and systems

- web-server: `routes/tasks.py` (list, detail, delete, status/update routes),
  `routes/projects.py` (project task list), `services/agent_service.py`
  (progress emission), `services/task_log_store.py`, models and Alembic.
- Operators: no new env var; the #904 retention cap also covers these rows.

## Constraints

- Reuse the #904 store, its session factory and migration chain.
- No query per task on the task list.
- Keep the sync `spec_to_task` chain; keep the diff in `routes/tasks.py`
  local (another lane edits `get_task_logs` for #906).
- Do not touch factory-gitops (#273).

## Open questions

None. The owner chose Postgres for the 2026-10-23 wave.
