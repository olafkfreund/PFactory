---
status: approved
issue: 901
spec: spec/2026-10-22-901-orphan-job-states.md
---

# Plan: end orphaned `job_states` rows in the stale-ingest sweep

Approved decisions: owner chose option B (PFactory ends abandoned ingested
work itself). The sweep also ends `pfactory` `job_states` rows that are
`queued`, `ended_at IS NULL`, created before `now - TTL`, and whose `job_id` is
not a session id from the sweep's own `plan_sessions` listing. They get
`service_status='discarded'`, `lifecycle_state=lifecycle_state_for('discarded')`
(`failed`), `ended_at=now`, and an `error` naming the orphaned session. Select
candidates first, then a conditional UPDATE with the same predicate (no
unbounded `NOT IN`, no join to `plan_sessions`). Log the count; never raise.

## Steps

1. `apps/web-server/server/jobstore/store.py` (after `_reclaim_leaseless_terminal_on`):
   add `end_orphaned_queued(older_than: datetime, live_ids: set[str]) -> int`
   and its `_async` coroutine as described.
   → verify by `ruff check` on the file.
   Traps: sync method must marshal through `self._run`; use
   `.execution_options(synchronize_session=False)` like the reclaim UPDATEs.
2. `apps/backend/plan/service.py` `discard_stale_ingested` (~line 1846): keep
   the listed sessions, and after the loop call
   `getattr(self._job_store, "end_orphaned_queued", None)` with
   `current - timedelta(seconds=max_age_seconds)` and the listed ids; log
   `stale ingest sweep ended %d orphaned job_states row(s)` at info when > 0;
   catch `Exception` and log a warning. Import `timedelta`.
   Traps: `_job_store` may be `None` or a test fake without the method.
3. `tests/test_plan_orphan_sweep.py` (new): SQLite `JobStateStore` fixture as in
   `tests/test_jobstore_state.py`; tests: old orphan ended with reason and
   `ended_at`; fresh orphan untouched; row with a live session untouched;
   `running`/`review`/terminal rows untouched; the service sweep ends an orphan.
   → `apps/backend/.venv/bin/pytest tests/test_plan_orphan_sweep.py tests/test_plan_discard.py tests/test_jobstore_state.py -q`

## Tests

The pytest command above passes; it fails without step 1/2 (method missing,
orphan stays `queued`). `ruff check` and `scripts/ratchet_lint.py` clean.

## Deviations

- Step 1: `end_orphaned_queued` returns `int(self._run(...))`, not the bare
  `self._run(...)` the other wrappers use. `_run` returns `Any`, so the bare
  form is a net-new mypy `no-any-return` error and the blocking ratchet fails
  (PR #902 CI, fix round 1).
- Step 2: the orphan pass runs only when the session list is complete. A new
  `PlanService._list_sessions()` returns `(sessions, complete)`
  (`_all_sessions()` wraps it); `complete` is False when the shared
  `plan_sessions` list failed and the local cache stood in, or a stored row did
  not parse. Then the pass is skipped with a warning, since a live session
  missing from the list would otherwise have its row ended (PR #902 review).
- Step 3: added `test_the_service_sweep_skips_orphans_when_the_session_list_failed`.
  The tests run on SQLite in `tests/` (backend CI job), not in the Postgres suite.

## Rollback

Revert the PR. Rows already ended stay ended (they were orphans).
