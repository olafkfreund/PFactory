---
status: approved
issue: 779
spec: spec/2026-09-26-779-shared-store-mode.md
---

# Plan: PFactory behaves, and is tested, the same with the shared session store as without it

Self-contained summary of the approved decisions:

- **Group 1, production fixes** (no service change): use the mutator's
  return value, or re-`get()`.
  - `routes/github.py` PR plan review renders from
    `await SERVICE.process_async(...)`'s returned session.
  - `routes/mcp_rpc.py` `_tool_get_task_contract` reads `contract_result` from
    `SERVICE.emit_contract(...)`'s returned session.
- **Group 2, tests that stage state on a held copy** persist it with a shared
  helper `persist(svc, session)` in `tests/conftest.py`. It calls
  `svc._save(session)` after learning the stored version, so it goes through
  the same compare-and-set path. Tests that read after a mutator use the
  return value or `svc.get(sid)`. Assertions change only where they relied on
  per-process object identity; each such change is listed under Deviations.
- **Group 3, determinism and isolation** (`tests/conftest.py`):
  - `apps/web-server` is on `sys.path` at import;
  - a session-scoped fixture migrates once when `DATABASE_URL` is set and
    `plan_sessions` is missing;
  - an autouse per-test fixture, when a store resolves, deletes
    `plan_sessions` rows, resets `plan_session_seq` and clears
    `_SESSION_STORE_UNAVAILABLE`. It refuses unless the database name
    contains `test`.
  - With no `DATABASE_URL`, nothing changes.
- **CI:** no workflow change. It is never made green by forcing
  per-process mode.

All work happens in the worktree `/tmp/.../scratchpad/pf-779` on
`fix/779-shared-store-mode`, never in the shared PFactory checkout. Postgres:
a dedicated container (e.g. `pf-pg-779`, port 55443), a database whose name
contains `test`, stopped afterwards.

## Steps

1. **Baseline, recorded.** On this branch (equal to `origin/dev`), run
   `DATABASE_URL=<fresh migrated pg16> pytest tests/ -m "not slow and not postgres"`
   and save the failing set (expected: the 30 from #779).
   → verify that the count and names match the intent.

2. **Group 3 first** (`tests/conftest.py`): the path, the session-scoped
   migrate fixture, the autouse truncate fixture with the `test`-name guard,
   and the `persist()` helper. Order matters: isolation first, so group 2
   results are not polluted by leftovers.
   → verify:
   - `tests/test_service_waive.py` alone with `DATABASE_URL` set shows no "No
     module named 'server'" warning and uses the store (log line "PFactory plan
     sessions are SHARED");
   - the three isolation failures (`test_plan_service` list,
     `test_tenant_scoping` list x2, `test_plan_concurrency`) pass;
   - the full `tests/` run without `DATABASE_URL` is unchanged green.

3. **Group 1 regression tests first, then the fixes.**
   - `test_github_plan_review` (existing, under `tests/`) already fails in
     store mode.
   - New `apps/web-server/tests/test_mcp_task_contract_store.py`: a temporary
     SQLite store (the pattern from #774's
     `test_store_after_boot_migrations.py`), a processed session, and then
     `_tool_get_task_contract` returns a contract. It runs in every CI job, not
     only the Postgres one.

   Run both first and see them fail, then fix `routes/github.py` and
   `routes/mcp_rpc.py` to use the returned session.
   → verify that both pass, and that `grep` finds no read of the held variable
   after the mutator in either route.

4. **Group 2, file by file:** test_service_waive, test_service_emit_contract,
   test_readiness_staleness, test_readiness_criteria_consistency,
   test_plan_suggestions_apply, test_plan_write_routes_tenant_guard,
   test_plan_usage, and the remaining test_tenant_scoping test. Each file is
   done when it passes in both modes.
   → verify per file, with and without `DATABASE_URL`.

5. **Full gates:**
   - the store-mode full run (step 1's command) gives 0 failed;
   - the `ci.yml` invocations without `DATABASE_URL` stay green;
   - `tests/postgres` on pg16 is green, including
     `test_p1_suite_against_postgres` (run with the backend venv reachable so
     it does not skip);
   - `scripts/ratchet_lint.py --base origin/dev --package apps/backend
     --package apps/web-server --package scripts` (CI's exact form), and
     `uvx ruff@0.15.17` format and check on the changed files.

   Commit with the hook, never `--no-verify`. If the docker tests time out,
   wait for `/proc/loadavg` below 16 and retry once.
   → verify all green.

6. **PR** to `dev` with `Fixes #779`, linking the intent, spec and plan. The
   PR description lists the two production bugs it fixes. Merge when CI is
   green (except `secrets (P2)`, #780, which fails on every PR) and every
   review thread is addressed.
   → verify CI.

7. **Unblock #774:** update PR #778 from `dev` (`gh pr update-branch 778`) and
   confirm its Postgres jobs go green unchanged. Then merge #778 (the #774
   plan resumes at its own step 7: release 0.6.21 with the `package-lock.json`
   versions, deploy, check the startup log). The 0.6.21 release notes list the
   two production fixes from this task.
   → verify, in production after deploy:
   - a GitHub PR plan review posts a comment with a real gate result (on the
     demo repo; a throwaway PR is opened and closed afterwards);
   - the MCP task-contract tool returns a contract for a processed session;
   - the pod log shows "SHARED" with no "not ready" warning.

   **The KEDA pin is not changed.**

## Tests

```bash
cd <pf-779 worktree>
export PATH=/mnt/data/Source-home/GitHub/PFactory/apps/backend/.venv/bin:$PATH
U=postgresql+asyncpg://pfactory_test:pfactory_test@localhost:55443/pfactory_test
DATABASE_URL=$U python -m pytest tests/ -m "not slow and not postgres" -q     # store mode: 0 failed
python -m pytest tests/ apps/web-server/tests/ -m "not slow" -q               # per-process: green
TEST_POSTGRES_URL=$U python -m pytest tests/postgres -q                       # includes the P1 suite
python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts
```

## Rollback

- **Group 1:** revert the two route changes (restores the stale reads).
- **Groups 2 and 3:** test-only; revert freely.

There is no schema or config change.

## Deviations

- **Group 3, schema:** the migration runs from `pytest_sessionstart`, not from
  a session-scoped fixture. Collection already builds `SERVICE`
  (`plan.agent_api` and `tests/test_pfactory_mcp_rpc.py` import it at module
  level), and a store resolved before the table exists marks the URL
  unavailable, so a fixture would run too late and leave the run
  per-process. It runs `alembic upgrade head` unconditionally when
  `DATABASE_URL` is set, which is a no-op on a database already at head. The
  `test`-name guard runs before the migration as well as before each wipe, so
  a non-test database is never touched.
