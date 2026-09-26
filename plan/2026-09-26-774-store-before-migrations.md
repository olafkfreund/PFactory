---
status: approved
issue: 774
spec: spec/2026-09-26-774-store-before-migrations.md
---

# Plan: a pod that migrates on boot uses the shared session store without a restart

Self-contained summary of the approved decisions:

- **New `plan/service.py` function**
  `attach_session_store_after_migrations() -> bool`:
  - `DATABASE_URL` unset: return False.
  - Discard the URL from `_SESSION_STORE_UNAVAILABLE`, once per call.
  - `SERVICE` not constructed yet (`"SERVICE" not in globals()`): return False.
  - `SERVICE` already has a store: run the replica guard and return True.
  - Otherwise `_resolve_session_store()`. If a store comes back, then under
    `SERVICE._store_lock` assign it, run `_import_sessions_into_store()`, and
    log INFO "plan sessions attached to the shared store after boot migrations
    (#774)".
  - Finally run `_warn_if_multi_replica_without_store` on the result, which is
    where `PFACTORY_REQUIRE_SHARED_STORE=1` refuses. Return whether a store is
    attached.
- **New module switch** `_DEFER_REPLICA_GUARD` plus `defer_replica_guard()`.
  While it is set, `PlanService.__init__` skips the replica guard. Only
  `server/main.py` sets it, before the routers are included. The CLI and
  tests are unchanged.
- **`server/main.py` lifespan:** right after `await init_db()`, run
  `await asyncio.to_thread(attach_session_store_after_migrations)`. This runs
  in both `MIGRATIONS_AUTO_APPLY` modes.
- **No per-request retry, and no readiness-probe change.**
- **Docs:** `guides/shipping.md` "Running more than one replica" gets three
  points: attach in the same boot, the confirming log line, and that the
  require-shared-store refusal now happens after migrations.

All work happens in the worktree `/tmp/.../scratchpad/pf-774`, on
`fix/774-store-before-migrations`, never in the shared PFactory checkout.

## Steps

1. **Harness check.** Confirm whether the Alembic migrations run on SQLite:
   `DATABASE_URL=sqlite+aiosqlite:///<tmp>.db python -m alembic upgrade head`
   from `apps/web-server`.
   - If they do, tests 1 to 3 below use a tmp SQLite file.
   - If they don't, tests 1 and 2 build the tables from the models
     (`PlanSessionRow`/`PlanSessionCounter`, as `tests/postgres` does), and
     test 3 goes under `tests/postgres` against a local Postgres 16 (a
     dedicated DB, never another session's container).

   Record the outcome as a deviation if it changes a test's location.
   → verify with the command's exit status and `alembic current`.

2. **Tests first:** `apps/web-server/tests/test_store_after_boot_migrations.py`,
   or `tests/postgres/` per step 1. Each test resets the module state it
   touches (`_SESSION_STORE_UNAVAILABLE`, `_SESSION_STORE_CACHE`, `SERVICE` in
   `plan.service.__dict__`, `_DEFER_REPLICA_GUARD`) with `monkeypatch`, the way
   `tests/test_plan_service.py` handles the store cache.
   1. Attach after migrating: `SERVICE` is constructed with no table (store
      None, URL marked unavailable). Create the tables, then call attach:
      True, the store is set, a pre-seeded on-disk session is imported, and a
      second call is a no-op returning True.
   2. Deferred guard: with `defer_replica_guard()`,
      `PFACTORY_REPLICA_COUNT=2` and `PFACTORY_REQUIRE_SHARED_STORE=1`,
      constructing with no table does not raise. Attach after creating the
      tables returns True. Attach with the tables still missing raises the
      refusal.
   3. App-level: `TestClient(create_app())` (see
      `apps/web-server/tests/test_tracing.py` for how the app is built in
      tests) against an unmigrated DB with `MIGRATIONS_AUTO_APPLY=true`. After
      the lifespan startup, `SERVICE._session_store` is not None and the
      attach log line was emitted (`caplog`).

   → verify by running them on the current code: 1 and 2 fail on the missing
   functions, and 3 fails with the store None.

3. **`apps/backend/plan/service.py`:** add `_DEFER_REPLICA_GUARD`,
   `defer_replica_guard()` and `attach_session_store_after_migrations()`, and
   the guard skip in `__init__`, each with a one-line reason comment citing
   #774. Keep `_resolve_session_store` unchanged; the attach function reuses
   it.
   → verify with tests 1 and 2.

4. **`apps/web-server/server/main.py`:** call `defer_replica_guard()` before
   the routers are included (at the top of `create_app()`, before any
   `include_router` or route-module import that reaches `SERVICE`). Call
   `attach_session_store_after_migrations` via `asyncio.to_thread` right after
   `await init_db()` in the lifespan hook.
   → verify with test 3 and the existing startup-related tests
   (`apps/web-server/tests -k "main or lifespan or startup or tracing"`).

5. **Docs:** extend `guides/shipping.md` "Running more than one replica" as in
   the summary.
   → verify by grep for "after boot migrations" in the guide.

6. **Gates:**
   - the `ci.yml` suites: `pytest tests/ apps/web-server/tests/ -m "not slow"`
     and `pytest apps/backend -o asyncio_mode=auto`;
   - `tests/postgres` against a local Postgres 16 (a dedicated container or DB
     only), including the #758 two-process tests;
   - `scripts/ratchet_lint.py --base origin/dev`, with the venv `bin` first on
     `PATH`;
   - `uvx ruff@0.15.17 format --check` and `check` on the changed files.

   Commit with the hook (never `--no-verify`). If the hook's docker tests time
   out, wait for the host load to drop (`/proc/loadavg` 1-minute below 16) and
   retry once.
   → verify all green.

7. **PR** to `dev` with `Fixes #774`, linking the intent, spec and plan. Merge
   when CI is green and the review threads are resolved. Then bump the version
   (0.6.21) on `dev` via a `chore/release-0.6.21` PR, including the
   `package-lock.json` root and workspace versions (#773), and sync
   `dev -> main`. Watch `deploy.yml`.
   → verify: the prod image shows the new SHA; the new pod's log has the
   attach line (or, since this release carries no new migration, the
   "SHARED ... plan_sessions" line with no "not ready" warning); and
   `plan_sessions` rows are unchanged or growing. **Do not change the KEDA
   pin.**

## Tests

```bash
cd <pf-774 worktree>
export PATH=/mnt/data/Source-home/GitHub/PFactory/apps/backend/.venv/bin:$PATH
pytest apps/web-server/tests/test_store_after_boot_migrations.py -q -o asyncio_mode=auto
pytest tests/ apps/web-server/tests/ -m "not slow" -q
TEST_POSTGRES_URL=postgresql+asyncpg://pfactory_test:pfactory_test@localhost:<port>/<own db> pytest tests/postgres -q
python scripts/ratchet_lint.py --base origin/dev
```

## Rollback

Revert the fix commit on `dev` and cut a patch release. There is no schema or
config change. After a rollback, the first boot after a future migration
needs a manual restart again, which was the pre-fix behaviour.

## Deviations

Recorded during implementation; none changes an approved spec decision.

- **Step 1 outcome, no location change.** `alembic upgrade head` runs on
  SQLite (exit 0, `alembic current` at head `d4a7e2b9f1c6`, `plan_sessions`
  created), so all three tests live in
  `apps/web-server/tests/test_store_after_boot_migrations.py` on tmp SQLite.
- **`tests/test_plan_service.py`, two tests pinned.** `create_app()` sets
  `_DEFER_REPLICA_GUARD` for the process, so any test that builds the app
  earlier in a full run left it set, and the two tests asserting the
  construction-time guard (#755) failed. Each now sets the switch to False
  with `monkeypatch`.
- **Log capture uses a handler on `plan.service`, not `caplog`.**
  `create_app()` reconfigures the root logger that `caplog` reads through;
  this mirrors `test_tracing.captured()`.
- **Test 3 reads `SERVICE` after `create_app()`.** When an earlier test has
  already imported `routes.plan_pipeline`, `create_app()` does not rebuild
  `SERVICE`. Reading it before the lifespan runs builds it pre-migration
  either way, and the test asserts its store is None at that point.
- **"`SERVICE` not constructed" is `isinstance(..., PlanService)`,** not
  `"SERVICE" not in globals()`, so a test that monkeypatches a stand-in
  `SERVICE` is also left alone. Same result for the real singleton.
