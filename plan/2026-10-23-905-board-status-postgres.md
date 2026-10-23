---
status: approved
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

## Deviations (implementation)

- `GET /api/tasks` keeps its `Task` response shape (no `executionProgress`, as
  before): from a pod without the spec directory it returns the same status,
  phase and subtasks (the progress counts) as the owning pod. `executionProgress`
  comes back on `GET /api/projects/{id}/tasks` (the board) and `GET /api/tasks/{id}`.
- Steps 1-7 were done in one coder run rather than step by step; the coder's
  extra `Task.execution_progress` field was removed in review.
- The `_emit_progress` hook has no dedicated test; it calls the same
  `board_record` builder and `schedule_board` the status-route test covers.

## Deviations (review fix round 1)

Review of PR #908 found end and create paths that never wrote a row.

- Step 5 widened: `_emit_progress` only finds the spec dir while the run is
  tracked, and stop, crash and pod-exit paths clear `_spec_dirs` first. The
  snapshot is now also scheduled inside `_update_plan_status` after it writes
  `test_plan.json` (helper `AgentService._schedule_board`). Every end path writes
  there.
- `_fail_interrupted_run` (pod exit or monitor crash) saves the row inline with
  `AgentService._save_board_now`, because a spawned flush may never run once the
  pod is going down.
- Step 3 widened: `create_task`, `approve_plan` and `reject_plan` call
  `_refresh_board`. The other spec-dir creators also refresh:
  `projects.py` create (`_refresh_board` instead of `spec_to_task`),
  `github.py` issue import and `auto_fix_service.start_auto_fix`. These files were
  not in the plan.
- `wait_for_task` is unchanged: it writes no file, so the snapshot built from
  files would be the same.
- Tests: `test_stop_and_interrupted_runs_reach_remote_board` (stop, then pod exit)
  and `test_created_task_reaches_remote_board`. Both fail without the fix. They
  cover the `_update_plan_status` hook, not `_emit_progress` (see round 2).

## Deviations (review fix round 2)

Review of PR #908 (head c07fb58) found more board-facing writers without a refresh
and no test of the mid-run hook.

- `archive_tasks` and `unarchive_tasks` (`projects.py`) and `recover_task`
  (`execution.py`) write `test_plan.json` and now call `_refresh_board`.
  `execution.py` was not in the plan.
- The round 1 note was wrong: its tests go through `_update_plan_status`, not
  `_emit_progress`. `test_progress_event_reaches_remote_board` now drives
  `_emit_progress` with `_spec_dirs` set and checks the remote board shows the new
  phase and subtasks. `test_archive_and_recover_reach_remote_board` covers the
  three routes. Each fails when its hook is removed.
- Not done: `create_pr_from_task` and the worktree sync paths were not traced.

## Deviations (review fix round 3)

- `start_task` in `apps/web-server/server/routes/execution.py`: the plan-review early return writes `human_review` to test_plan.json without starting a run, so no progress event follows. It now calls `_refresh_board` before `emit_task_status`. Test: `test_plan_review_hold_reaches_remote_board`, which fails without the call.
- Mid-run worktree sync (`_sync_worktree_files` in `apps/web-server/server/services/agent_worktree_sync.py`): every 3 s it copies subtask progress into the main spec dir, but only phase events rebuilt the board row. It now schedules a board flush when it synced files; flushes are coalesced per task. Test: `test_worktree_sync_reaches_remote_board`, which fails without the call.
