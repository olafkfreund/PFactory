---
status: draft
issue: 779
author: Olaf Krasicki-Freund
---

# Intent: PFactory behaves, and is tested, the same with the shared session store as without it

## Problem

Since the 0.6.20 restart (2026-09-24), production PFactory runs with the
shared Postgres session store (#757, #758): every `get()` returns a fresh copy
read from the store, and every save is compare-and-set. The test suite has
never exercised that mode:

- `tests/postgres/test_p1_suite_against_postgres.py` reruns the suite with
  `DATABASE_URL` pointed at Postgres. The first `PlanService()` in the run is
  built before anything migrates the database, so `_resolve_session_store`
  marks the URL unavailable for the whole process. Every later `PlanService()`
  silently stays per-process.
- Whether the store is even importable depends on test order: run alone,
  `tests/` cannot import `server.jobstore` ("No module named 'server'"). In a
  full run, an earlier test puts the web-server on `sys.path`.

Run in shared-store mode (clean `origin/dev`, migrated Postgres 16,
2026-09-26): **30 failed, 5087 passed**, in 13 files. They fall into three
groups:

1. **Callers that keep a `PlanSession` across a service call (a live
   production bug).** Confirmed: `apps/web-server/server/routes/github.py`
   (~547) ingests, calls `process_async(session_id)`, then renders the PR
   review comment from the ingest-time object. In production that comment
   reports no gate result (`test_github_plan_review`). Other call sites of the
   same shape may exist; they have not been audited yet.
2. **Tests that build state by mutating the returned object without saving
   it** (e.g. `session.epic = ...` and `session.review = ...` in
   `tests/test_service_waive.py`). This works only when `get()` returns the
   same in-memory object. It accounts for most failures: "process the plan
   before waiving/approving/emitting a contract" (17), "has no suggestions to
   apply" (6), `review is None` (4).
3. **Test isolation.** The shared store persists across tests, so tests that
   count or list sessions see every earlier test's sessions
   (`test_plan_service` `257 == 1`, `test_tenant_scoping` lists,
   `test_plan_concurrency` `253 == 40`).

Unconfirmed: `test_plan_usage` (usage 0 instead of 321) may be group 1 or 2.

This blocks #774 (PR #778): #774 correctly clears the "unavailable" mark after
boot migrations, which makes the Postgres suite run in shared-store mode and
surface these failures.

## Proposed outcome

- Every production call site that reads a session after mutating it through
  the service uses the post-call state. The GitHub PR review comment shows the
  real gate result.
- The suite passes in both modes (per-process and shared store), and CI runs
  the shared-store mode for real rather than by accident of test order.
- Tests that need a session in a particular state get it through the service
  (or an explicit save), not by mutating a returned copy.
- Each test sees only the sessions it created, in both modes.
- #778 (#774) can merge with its Postgres suite green, with no change to #774.

## Affected users and systems

- PFactory production: the GitHub PR plan-review comment (confirmed), plus
  any other group-1 call sites the audit finds.
- PFactory test suite and CI (`tests/postgres`, the `backend` and `critical`
  jobs).
- Release 0.6.21 (#774), which waits on this.

## Constraints

- No production behaviour change beyond fixing stale reads. The
  store/lease/CAS design (#757, #758) stays.
- CI must not be made green by forcing the Postgres suite back to
  per-process. Shared-store mode has to be exercised.
- Fixes to tests change how they set state up, not what they assert, unless
  an assertion itself depended on the per-process identity.
- The suite's run time must stay reasonable (the Postgres job is ~5 minutes
  today).

## Open questions

1. **Test isolation in shared-store mode:**
   - (a) truncate `plan_sessions` and reset the counter before each test (an
     autouse fixture when a store is configured);
   - (b) a fresh database per test module;
   - (c) run unit tests per-process by default and exercise shared-store mode
     in a dedicated, explicit subset.

   **Recommended: (a)**. It is cheap, it keeps the whole suite as the
   shared-store check, and it matches what production looks like to one test.
2. **Making the mode deterministic:** put `apps/web-server` on the path in
   `tests/conftest.py`, so the store's availability no longer depends on test
   order? **Recommended: yes.** Order-dependent coverage is how this went
   unnoticed.
3. **Scope of the group-1 audit:** only `routes/`, or also `apps/backend`
   callers (CLI, MCP, agents)? **Recommended: everything that calls a
   `PlanService` mutator and then reads a `PlanSession` it held from before
   the call.** Grep-driven, with a regression test per site found.
