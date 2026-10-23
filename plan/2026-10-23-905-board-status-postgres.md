---
status: draft
issue: 905
spec: spec/2026-10-23-905-board-status-postgres.md
---

# Plan: board status in the shared Postgres store

Approved decisions (from the spec):

- Owner decision: persist the board-facing status to Postgres. Rejected: routing
  board reads to the owning pod (lost on restart/scale-in), async per-task readers,
  writing on every read.
- One table `task_board_states(task_id PK, project_id indexed, record JSON,
  updated_at DB clock)`. `record = {"task": Task JSON without repo, "progress":
  get_execution_progress() or None, "archive": {archivedAt, archivedInVersion}}`.
- Same gate as #904: `task_log_store.enabled()` (engine not SQLite). File is the
  source whenever the spec dir is local; the record is read only when it is not.
- Writes from the pod holding the spec dir: `_emit_progress`, task status PATCH,
  task update. Coalesced per task; the snapshot is built at flush time.
- Reads keep the sync chain: async routes do one `load_boards(project_ids)` query
  and pass records down. `task_to_dict(task, board=None)`.
- Retention: `task_log_store.delete` and `prune` (same env var) cover the table.

## Steps

1. `apps/web-server/server/database/models.py` (after `TaskLogEntry`, ~475): add
   `TaskBoardState`. New migration
   `server/database/alembic/versions/20261023_c7e4a2d9b1f3_task_board_states.py`,
   revision `c7e4a2d9b1f3`, down_revision `b3d9e1f7a5c2`.
   → verify `tests/postgres/test_p1_alembic.py` head check (CI) and model import.
   Traps: JSON column (not JSONB) as #904 did; index name `ix_task_board_states_project_id`.
2. `apps/web-server/server/services/task_log_store.py`: `save_board`, `load_board`,
   `load_boards`, `schedule_board` (coalescing flusher via
   `server.background.tasks.spawn`); extend `delete` and `prune`.
   Traps: catch and log DB errors, never raise into the run.
3. `apps/web-server/server/routes/tasks.py`: extract the progress/archive block of
   `task_to_dict` into `_spec_extras(spec_dir, subtasks)`; `task_to_dict(task,
   board=None)`; add `board_record(project_id, spec_dir)` and
   `_refresh_board(project_id, spec_dir)`; call `_refresh_board` in
   `update_task_status` and `update_task` before returning; `list_tasks` and
   `get_task` fall back to records for tasks without a local spec dir.
   Traps: do not touch `get_task_logs` (#906 lane); keep the diff local.
4. `apps/web-server/server/routes/projects.py` `list_project_tasks` (~1088): append
   records for tasks without a local spec dir (one `load_boards` call).
5. `apps/web-server/server/services/agent_service.py` `_emit_progress` (~199): at the
   end, if enabled and `_spec_dirs` has the task, `schedule_board` with a builder
   that lazily imports `routes.tasks.board_record` (existing local-import pattern).
6. `docs/dev/environment-reference.md`: retention row mentions `task_board_states`.
7. Tests `apps/web-server/tests/test_task_board_store.py` per the spec's
   Verification list.

## Tests

`cd apps/web-server && .venv/bin/python -m pytest tests/test_task_board_store.py
tests/test_task_log_store.py -q` green; `ruff check` and `ruff format --check` on
touched files; `python scripts/ratchet_lint.py` no new findings.

## Rollback

Revert the PR. The migration is additive; `alembic downgrade b3d9e1f7a5c2` drops
the table, and older code ignores it.
