---
status: draft
issue: 829
spec: spec/2026-10-23-829-task-logs-postgres.md
---

# Plan: task logs in the shared Postgres store

Approved decisions, carried over from the spec:

- Owner decision: option 2. The per-task log record goes into the shared Postgres store.
  Rejected: RWX storage (NFS fails POSIX mkdir on the mount root, Factory#959),
  routing reads to the owning pod (lost on restart or scale-in), a single JSON blob row
  per task (read-modify-write races), dropping the file (breaks the same-pod readers).
- Tables `task_log_phases` (PK `task_id, phase`; `status`, `started_at`,
  `completed_at` strings; `updated_at` on the DB clock) and `task_log_entries` (`id`
  autoincrement PK; `task_id`, `phase`, `entry` JSON, `created_at` on the DB clock;
  index `task_id, id`). `task_id` = `"<project_id>:<spec_id>"`.
- "Shared store configured" = the engine dialect is not SQLite (`DATABASE_URL` is set
  to Postgres). Reuse `server.database.engine.async_session_factory`. No new client.
- `TaskLogWriter` still writes the file. With a `task_id` and the shared store it also
  queues entries and phase changes in order, and one flusher per writer drains them.
  A DB error logs ERROR and drops the batch; the task never fails because of it.
- Only the worktree writer gets the `task_id`.
- `get_task_logs` reads DB first and returns the same shape. It falls back to the
  file when there are no rows or no shared store. `load_spec_metadata` and
  `get_execution_progress` stay on the file (pod-local spec directory; the gap is
  noted in the spec).
- Retention: rows are deleted in `delete_task`. `PFACTORY_TASK_LOG_RETENTION_DAYS`
  (default 90, 0 = off) is applied by `prune()`, which runs in the background at
  each task run start.
- The backend `apps/backend/task_logger` does not change.

## Steps

1. `apps/web-server/server/database/models.py` (after `RunLease`, ~l.441): add
   `TaskLogPhase` and `TaskLogEntry`. New migration
   `server/database/alembic/versions/20261023_b3d9e1f7a5c2_task_logs.py` revises
   `a8d3e6b2c9f4` and creates both tables plus the index.
   → verify with `pytest tests/postgres/test_p1_alembic.py` (single head) where
   Postgres is available, and in CI.
   Traps: `BigInteger` autoincrement does not autoincrement on SQLite, so use
   `BigInteger().with_variant(Integer, "sqlite")`. Use `DateTime(timezone=True)` with
   `server_default=func.now()`.
2. New `apps/web-server/server/services/task_log_store.py`: `enabled()`,
   `retention_days()`, `write(task_id, ops)`, `load(task_id) -> dict | None`,
   `delete(task_id)`, `prune()`. The age comparison uses the DB clock, as
   `run_leases._now` does (Postgres `now() - make_interval`, SQLite `strftime`).
   Every function except `load` swallows errors and logs ERROR.
   → covered by step 7's tests.
3. `apps/web-server/server/services/task_log_writer.py`: `__init__(spec_dir, task_id=None)`;
   `_queue_db(op)` appends to `self._db_ops` and starts a flusher via `spawn`
   when none is running and a loop is running; `async def flush_db()` for tests.
   Queue an `entry` op in `add_entry` and a `phase` op wherever a phase's status
   or timestamps change (`add_entry` pending→active, `set_phase_status`).
   → `pytest apps/web-server/tests/test_task_log_store.py`.
   Traps: keep the file write and the WS emit unchanged; keep the methods synchronous.
4. `apps/web-server/server/services/agent_service.py` ~l.1097: pass
   `task_id=task_id` to the worktree `TaskLogWriter` only, and
   `spawn(task_log_store.prune())` when the store is enabled.
   → existing agent_service tests.
5. `apps/web-server/server/routes/tasks.py`: in `get_task_logs` (~l.2027), when
   `task_log_store.enabled()`, call `await task_log_store.load(task_id)` and use its
   document in place of the file's; otherwise run the existing lookup. In
   `delete_task` (~l.1539), `await task_log_store.delete(task_id)` after the
   `rmtree`. → step 7.
6. `docs/dev/environment-reference.md` (next to the `PFACTORY_RUN_LEASE_*` rows): add a
   `PFACTORY_TASK_LOG_RETENTION_DAYS` row that also describes the tables.
7. Tests: `apps/web-server/tests/test_task_log_store.py` (in-memory shared-cache
   SQLite as in `test_run_leases.py`, with `task_log_store.async_session_factory`
   and `enabled` patched) and `tests/postgres/test_task_log_store_postgres.py`
   (pattern of `test_run_leases_race.py`).

## Tests

- `cd apps/web-server && .venv/bin/pytest tests/test_task_log_store.py -q` passes.
  The cross-pod test fails without step 5.
- Web-server and backend suites (pre-commit hook), `ruff check`, `ruff format --check`,
  `scripts/ratchet_lint.py`.
- CI `postgres-acceptance` runs the Postgres test.

## Rollback

Revert the PR. The migration's `downgrade()` drops both tables. The file path never
stopped being written, so nothing is lost.
