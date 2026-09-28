---
status: draft
issue: 777
intent: intent/2026-09-28-777-job-store-after-migrations.md
---

# Spec: a pod that migrates on boot also gets the durable job-state store without a restart

Decisions carried from the approved intent:

1. Extend #774's post-migration hook to attach the job store too, and rename
   it `attach_stores_after_migrations()`. The old name
   `attach_session_store_after_migrations` stays as an alias for one release.
2. Nothing is in flight when the hook runs. Confirmed below, so attaching is
   a plain assignment.

## Design

### Facts from the code (`origin/dev`, 2026-09-28)

- `PlanService.__init__` sets `self._job_store = job_store if job_store is not
  None else _resolve_job_store()` (`apps/backend/plan/service.py:619`).
- `_resolve_job_store()` (line 297) caches only a working store
  (`_JOB_STORE_CACHE`). On a missing table it closes the store, logs "job_states
  table is not ready", and returns None **without** marking the URL
  unavailable, so calling it again later re-checks.
- `_job_store` is read only at call time: `_mirror` (~799), `process_async`
  (~1259, choosing `_durable_admit` or the in-process semaphore) and
  `_durable_admit` (~1303). Nothing else is derived from it at construction,
  so assigning it later is sufficient.
- The existing hook `attach_session_store_after_migrations()` (line 532) is
  called only from `server/main.py:106-108`, right after `await init_db()`,
  and from #774's tests.

### Nothing in flight (intent question 2)

The hook runs inside the lifespan startup, and FastAPI serves no request until
the lifespan startup completes. Admission (`process_async`) and transitions
(`_mirror`) run only inside requests or work started by them. So when the job
store is attached there is no semaphore-held or in-memory queued work to hand
over, and the assignment needs no migration of state.

### The change (`plan/service.py`)

- Rename `attach_session_store_after_migrations` to
  `attach_stores_after_migrations() -> bool`, and add after it:
  `attach_session_store_after_migrations = attach_stores_after_migrations  # #777 alias, remove after 0.6.22`.
- Inside, after the existing session-store block and before the replica
  guard:

  ```python
  if service._job_store is None:
      job_store = _resolve_job_store()
      if job_store is not None:
          service._job_store = job_store
          logger.info("plan state attached to the durable job-state store after boot migrations (#777)")
  ```

- **Return value unchanged:** whether a **session** store is attached (#774's
  contract and tests). The job store's result is observable through the log
  line and `SERVICE._job_store`. Folding it into the return value would change
  the meaning of `True` for existing callers.
- The docstring mentions both stores and #777.

### `server/main.py`

The lifespan imports and calls `attach_stores_after_migrations` (the new
name), still via `asyncio.to_thread` right after `init_db()`.

### Docs

`guides/shipping.md` "Running more than one replica" (#771 and #774): one line
saying the durable job-state store (admission and the KEDA queue signal) is
attached the same way, with its log line.

## Alternatives rejected

- **A separate `attach_job_store_after_migrations()`** (intent option b): two
  hooks for one root cause, and two lifespan calls to keep in step.
- **Mark the job-store URL unavailable, like the session store, and clear
  it in the hook**: that adds state for no benefit, since
  `_resolve_job_store` already re-checks on every call.
- **Return a tuple or dataclass of both results**: it changes #774's contract
  and tests for no caller that needs it.

## Risks

- **Double resolution.** If the job store resolved at construction, the hook
  skips it (`is not None`). If it did not and the table is still missing,
  `_resolve_job_store` logs its "not ready" warning a second time. That is
  acceptable and accurate.
- **The alias outlives its release.** The comment names 0.6.22 for removal,
  and a follow-up issue is filed when this merges.

## Verification

- `apps/web-server/tests/test_store_after_boot_migrations.py`, extended in the
  same SQLite style:
  1. `SERVICE` built with no tables, so both stores are None. Migrate, then
     call `attach_stores_after_migrations()`: `_job_store` is not None, and the
     #777 log line appears.
  2. The app-level boot test (`create_app()` against an unmigrated DB with
     `MIGRATIONS_AUTO_APPLY=true`) now also asserts `SERVICE._job_store` is not
     None after startup.
  3. The alias: `attach_session_store_after_migrations is
     attach_stores_after_migrations`.
  4. Idempotent: a second call leaves the same `_job_store` object.

  Run tests 1 and 2 on the current code first: they fail on the job-store
  assertion.
- Gates:
  - the ci.yml suites;
  - the store-mode run (#779's conftest) on Postgres 16;
  - `tests/postgres` including the P1 suite;
  - `scripts/ratchet_lint.py` in CI's form (three `--package` flags);
  - ruff 0.15.17.

  Commit from a worktree that has the backend `.venv` symlinked, so the hook's
  ratchet is real (#786).
- Production: with no new migration in the next release, the startup log
  shows "plan state is DURABLE" at construction and no #777 attach line. That
  is correct. The attach line proves the fix on the next deploy that carries a
  `job_states` migration.
