---
status: approved
issue: 774
intent: intent/2026-09-26-774-store-before-migrations.md
---

# Spec: a pod that migrates on boot uses the shared session store without a restart

Decisions carried from the approved intent:

- Fix the ordering, with a one-shot re-resolve right after `init_db()`. No
  per-request retry.
- Failing readiness when there is no store is out of scope
  (`PFACTORY_REQUIRE_SHARED_STORE=1` stays the opt-in).

## Design

### Why it happens (from the code)

- `server/main.py` imports the route modules at app construction.
  `routes/plan_pipeline.py:31` does `from plan.service import (SERVICE, ...)`,
  which triggers the PEP 562 `__getattr__` (`plan/service.py:2065`) and
  constructs `PlanService()` then.
- `PlanService.__init__` (`plan/service.py:~583`) calls
  `_resolve_session_store()`. With no table, it closes the store and adds the
  URL to `_SESSION_STORE_UNAVAILABLE`. It then skips
  `_import_sessions_into_store()` and calls
  `_warn_if_multi_replica_without_store(None)`.
- The lifespan hook runs `await init_db()` (`server/main.py:102`) later. That
  applies the migrations when `MIGRATIONS_AUTO_APPLY=true` (the default).

**A second consequence the intent did not list:** with
`PFACTORY_REQUIRE_SHARED_STORE=1`, `_warn_if_multi_replica_without_store`
raises inside `__init__`, i.e. at import, before migrations. The first boot
after a store migration would crash the pod rather than refuse cleanly after
migrating. The fix below also covers this.

### The fix

1. **`plan/service.py`: a module-level post-migration hook.**
   `attach_session_store_after_migrations() -> bool`:
   - `DATABASE_URL` unset: return False (nothing to do).
   - Discard the URL from `_SESSION_STORE_UNAVAILABLE`, so a table that exists
     now is seen. This is the one sanctioned retry, once per process.
   - If `SERVICE` has not been constructed yet (`"SERVICE" not in globals()`):
     return False. Its lazy construction will resolve normally.
   - If `SERVICE._session_store` is already set: run the replica guard and
     return True (idempotent).
   - Otherwise, `store = _resolve_session_store()`. If it is not None:
     - assign `SERVICE._session_store = store`;
     - run `SERVICE._import_sessions_into_store()`;
     - log INFO "plan sessions attached to the shared store after boot
       migrations (#774)".
   - Finally, run the replica guard (`_warn_if_multi_replica_without_store`) on
     the result. The refusal under `PFACTORY_REQUIRE_SHARED_STORE=1` now
     happens here, after migrations.
   - Return whether a store is attached.

   Thread safety: take `SERVICE._store_lock` around the assignment and the
   import, as the other `_sessions` mutations do.

2. **Defer the guard at import time, only under the web server.** A
   module-level switch in `plan/service.py`:
   - `_DEFER_REPLICA_GUARD = False`, and `defer_replica_guard() -> None` sets
     it True.
   - `PlanService.__init__` skips `_warn_if_multi_replica_without_store` while
     the switch is set.
   - `server/main.py` calls `plan.service.defer_replica_guard()` before it
     includes the routers, so before `SERVICE` can be constructed.

   The CLI, tests and any other `PlanService()` construction keep today's
   immediate check.

3. **`server/main.py` lifespan:** right after `await init_db()`, run
   `await asyncio.to_thread(attach_session_store_after_migrations)`. The store
   drives its own loop thread, so the blocking call stays off the event loop.
   This runs in both `MIGRATIONS_AUTO_APPLY` modes. With `false`, `init_db()`
   has already verified the schema is at head (or failed fast), and the call
   just attaches the store.

### Docs

`guides/shipping.md` "Running more than one replica" (added in #771) gains
three points:
- a pod that migrates on boot attaches to the shared store in the same boot,
  with no restart;
- the log line that confirms it;
- `PFACTORY_REQUIRE_SHARED_STORE=1` refuses after migrations, not before.

## Alternatives rejected

- **Run migrations before importing the routes** (move `init_db` out of the
  lifespan into module import or `create_app`). It puts async DB work at import
  time and changes when every other startup step runs. That is a wider change
  than the race needs.
- **Bounded lazy retry per request** (intent option b). It adds a store check
  to a hot path and reintroduces pool churn, the reason
  `_SESSION_STORE_UNAVAILABLE` exists. It could follow later if a genuine
  transient DB outage at boot ever matters.
- **Stop constructing `SERVICE` at import** (make every route resolve it
  lazily). It touches every route module; the hook achieves the same effect
  with one call.

## Risks

- **Requests between app construction and the end of the lifespan hook.**
  FastAPI serves no requests until the lifespan startup completes, so none
  can see the per-process store in that window.
- **Double import.** `_import_sessions_into_store` inserts with expected
  version 0 and skips existing rows (#758), so a second run is harmless.
- **`PFACTORY_REQUIRE_SHARED_STORE=1` and an unreachable DB** now fail at the
  lifespan hook instead of at import. Either way the pod does not become
  ready; the error is clearer and comes after migrations.

## Verification

- `apps/web-server/tests/test_store_after_boot_migrations.py`, using SQLite
  (the store and the migrations support it):
  1. A `DATABASE_URL` pointing to an empty SQLite file. Construct `SERVICE`:
     its store is None and the URL is marked unavailable. Apply the migrations
     (`alembic upgrade head` or the helper `init_db` uses), then call
     `attach_session_store_after_migrations()`. Expect: returns True,
     `SERVICE._session_store` is not None, and pre-existing on-disk sessions
     are imported. Calling it again is a no-op.
  2. `defer_replica_guard()` plus `PFACTORY_REPLICA_COUNT=2`,
     `PFACTORY_REQUIRE_SHARED_STORE=1`, and an unmigrated DB: constructing
     `SERVICE` does not raise, the attach after migrating succeeds, and the
     attach with the DB still unmigrated raises the refusal.
  3. App-level: `TestClient(create_app())` against an unmigrated SQLite with
     `MIGRATIONS_AUTO_APPLY=true`. After startup, `SERVICE._session_store` is
     not None and the "attached ... after boot migrations" log appears.

  Run the new tests on the current code first: they fail (the hook is
  missing; test 3 sees no store).
- Existing suites green, including `tests/postgres` (the #758 two-process
  tests) against a local Postgres 16, plus the ratchet and ruff 0.15.17.
- Production, after release: a deploy that carries a migration boots with the
  "attached ... after boot migrations" log, and `plan_sessions` gets rows
  without a restart. The next release that includes a migration is the real
  test; until then, check the log on the first boot of the release that ships
  this.
