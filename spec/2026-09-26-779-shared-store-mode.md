---
status: draft
issue: 779
intent: intent/2026-09-26-779-shared-store-mode.md
---

# Spec: PFactory behaves, and is tested, the same with the shared session store as without it

Decisions carried from the approved intent:

1. Test isolation: truncate the session store before each test (an autouse
   fixture, only when a store is configured).
2. The mode is deterministic: `tests/conftest.py` puts `apps/web-server` on
   `sys.path`, so the store's availability no longer depends on test order.
3. The group-1 audit is wide: every production caller that holds a
   `PlanSession` across a `PlanService` mutator.

Constraint carried: CI must exercise shared-store mode for real; it must not
be forced back to per-process.

## Design

### Group 1: production call sites (the audit)

Audit method: grep outside `tests/` for a `PlanService` mutator called with
`<var>.session_id` while `<var>` is read after the call (`apps/`), plus
review of every `SERVICE.ingest_*`/`get`/`process*` assignment. Result
(2026-09-26, `origin/dev`):

| Site | Stale read | Production effect today (shared store) |
|---|---|---|
| `apps/web-server/server/routes/github.py:~548-560` (PR plan review) | `session` from `ingest_text`, then `process_async(session.session_id)`; the comment is rendered from `session` | The PR review comment reports no gate result |
| `apps/web-server/server/routes/mcp_rpc.py:~232-255` (`_tool_get_task_contract`) | `sess` from `_resolve_session`, then `emit_contract(sess.session_id, dry_run=True)`; it returns `sess.contract_result` | The MCP task-contract tool answers "task contract could not be built" even though it built one |

Reviewed and correct: `plan/agent_api.py` (uses each call's return value),
`routes/plan_pipeline.py` suggestions/apply (uses `process_async`'s return
value or re-`get()`s), and `routes/plan_pipeline.py` ingest routes (return the
object they just received).

Fix: use the mutator's return value (`process_async` and `emit_contract` both
return the updated `PlanSession`). Where a site cannot, re-read with
`SERVICE.get(session_id)` after the call. No service change.

### Group 2: tests that mutate a returned copy

Tests set state by assigning onto a `PlanSession` they hold (e.g.
`session.epic = ...`, `session.review = ...`, then calling a mutator by id), or
read state from a copy held before a mutator (`test_plan_usage`). Fix, test by
test:

- **When the test stages state:** persist it through the service before the
  next call, with a small shared test helper `persist(svc, session)` in
  `tests/conftest.py` that calls `svc._save(session)`. That is the same save
  every production transition uses, so it goes through compare-and-set and the
  test stays honest. The helper learns the stored version first (`svc.get()`
  or the copy returned by the last call), so the compare-and-set does not fail
  on a copy that was never loaded.
- **When the test reads after a mutator:** use the mutator's return value or
  `svc.get(sid)`.
- Assertions are not changed, except where an assertion checked object
  identity that only the per-process mode provides; any such change is listed
  in the plan.

Affected files (from the 30): test_service_waive, test_service_emit_contract,
test_readiness_staleness, test_readiness_criteria_consistency,
test_plan_suggestions_apply, test_plan_write_routes_tenant_guard,
test_plan_usage, and part of test_tenant_scoping.

### Group 3: isolation and determinism (`tests/conftest.py`)

- **Path:** at conftest import, put `apps/web-server` on `sys.path` (next to
  the existing `apps/backend` handling), so `server.jobstore` imports in every
  run and in any order.
- **Schema:** a session-scoped fixture. When `DATABASE_URL` names a database
  (anything other than SQLite in-memory) and `plan_sessions` is missing, it
  runs `alembic upgrade head` once, so the mode is shared-store from the first
  test rather than after whichever test migrates first. When `DATABASE_URL` is
  unset, it does nothing (per-process, today's default local run).
- **Isolation:** an autouse function-scoped fixture. When a session store is
  resolvable, it deletes all rows from `plan_sessions` and resets
  `plan_session_seq` before each test, through the store's engine with bound
  statements. It also clears `plan.service._SESSION_STORE_UNAVAILABLE` so a
  stale "unavailable" mark from another test cannot leak. With no store, it
  does nothing.
- **Tests that count or list sessions** (`test_plan_service`
  `test_list_and_unknown_session`, `test_tenant_scoping` list tests,
  `test_plan_concurrency`) then pass unchanged, because each test starts empty.

### CI

No workflow change. `tests/postgres/test_p1_suite_against_postgres.py` already
reruns `tests/` with `DATABASE_URL` pointed at Postgres. With the schema
fixture, that inner run is shared-store for real. The per-process mode stays
covered by the plain `backend` and `critical` runs (no `DATABASE_URL`).

## Alternatives rejected

- **A fresh database per module** (intent option b): slower, and the counter
  and table are still shared within a module.
- **Force the Postgres suite to per-process**: hides exactly the class of bug
  this found in production. Rejected by the intent's constraint.
- **Make `PlanService.get()` return a shared in-memory object even with a
  store** (restoring object identity): that reintroduces the stale-cache
  problem #757 and #758 removed, and breaks compare-and-set semantics.

## Risks

- **Truncating before every test adds DB round trips.** There are two cheap
  statements per test, and only when a store is configured. That adds seconds
  to the ~5-minute Postgres job.
- **A fixture that misidentifies "store configured"** could truncate a real
  database. It acts only under pytest, only when `DATABASE_URL` is set, and
  the CI URLs are the test containers. A guard refuses unless the database
  name contains `test` (both CI and the local test URLs do), with a clear
  error otherwise.
- **The group-1 audit is grep-based and may miss a site.** Mitigated by
  running the whole suite in shared-store mode: any other site covered by a
  test now fails loudly.

## Verification

- **Group 1:** a regression test per site, in shared-store mode.
  - `test_github_plan_review` (existing) passes.
  - A new MCP test: `_tool_get_task_contract` on a processed session returns
    a contract with a store configured.
  - Both fail on current `dev` in shared-store mode.
- **Groups 2 and 3:** the full suite with `DATABASE_URL` set to a migrated
  Postgres 16, from a clean start (no pre-migration step): 0 failed, compared
  with 30 failed on `dev`. Also without `DATABASE_URL`: unchanged green.
- **Order independence:** `tests/test_service_waive.py` alone with
  `DATABASE_URL` set uses the store (no "No module named 'server'" warning),
  and passes.
- **CI:** #779's PR green, including `postgres (P1 acceptance, PG 15 and 16)`
  (`secrets (P2)` excepted, #780). After merge, #778 (#774) rebased and green
  with no change to #774.
- **Production, after release 0.6.21:** a GitHub PR plan review posts a comment
  with a real gate result, and the MCP task-contract tool returns a contract
  for a processed session.
