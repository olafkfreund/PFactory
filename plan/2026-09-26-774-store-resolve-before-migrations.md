---
status: approved
issue: 774
spec: spec/2026-09-26-774-store-resolve-before-migrations.md
---

# Plan: The first boot after a migration runs per-process, silently

Approved decisions (from the spec):

- Retry with a cooldown, not boot ordering. `_SESSION_STORE_UNAVAILABLE: set`
  becomes `_SESSION_STORE_RETRY_AFTER: dict[str, float]` (monotonic deadline);
  `_STORE_RETRY_COOLDOWN_SECONDS = 30.0`. An unready store is still closed.
- `PlanService._session_store` becomes a property over `self.__store` **with a
  setter** (tests and `__init__` assign it). First successful late resolution:
  cache, run the idempotent on-disk import, log the pickup at INFO, re-run
  `_warn_if_multi_replica_without_store`.
- `__init__` stops resolving eagerly; an injected store is never re-resolved.
- Each failed attempt (at most one per cooldown) keeps logging the existing
  WARNING, so a pod with no store does not fall silent.
- Known gap to state in the PR: a session created inside the cooldown window is
  written per-process and reaches the store on its next `_save`.

## Steps

1. `apps/backend/plan/service.py`: replace the negative-cache set with the
   deadline dict; `_resolve_session_store()` returns `None` while the deadline
   is in the future and stamps a new one on each failure (both the not-ready and
   the exception path). → verify by the cooldown tests (step 4b/4c).
2. Same file: `__init__` stores the injected value (or `None`) into
   `self.__store` and no longer calls `_resolve_session_store()`; the eager
   `_import_sessions_into_store()` + guard call move into the property's
   first-success path. Add the `_session_store` property + setter.
   → verify existing suites pass unchanged with no `DATABASE_URL`.
3. Confirm every existing read still goes through the property
   (`git grep -n "_session_store" apps/backend/plan/service.py`): no direct
   `self.__store` reads outside the property.
4. `tests/postgres/test_plan_session_store.py` (real Postgres):
   a. **#774 reproduction**: drop `plan_sessions`, construct the service,
      assert per-process; create the table; assert the SAME instance now uses
      the store, imported the on-disk sessions, and logged the pickup;
   b. unreachable URL: N resolution attempts build **one** store and close it;
      a further attempt inside the cooldown builds none;
   c. after the deadline passes (monkeypatched clock, no sleep) another attempt
      is made;
   d. an injected store is never re-resolved (resolver monkeypatched to fail).
5. `tests/test_plan_service.py` (no DB): no `DATABASE_URL` ⇒ no attempt, no
   warning; the existing guard tests still pass.
6. Negative control (not committed): pin the cooldown to infinity (the old
   permanent cache) → 4a fails. Restore.
7. Full check: `pytest tests/ -q -k "plan_service or plan_persistence or emit"`,
   `pytest tests/postgres/ -m postgres`, ruff + the mypy diff vs `dev`
   (the ratchet's mypy half is CI-only, so run it locally this time).

## Tests

    apps/backend/.venv/bin/pytest tests/test_plan_service.py tests/postgres/test_plan_session_store.py -q
    apps/backend/.venv/bin/pytest tests/postgres/ -m postgres -q
    apps/backend/.venv/bin/python -m mypy --config-file standards/mypy.ini \
      --explicit-package-bases --namespace-packages apps/backend/plan/service.py

Expected: all pass; no new mypy errors vs `dev`; full suite via the hook.

## Rollback

Revert the commit: resolution returns to eager-at-construction with a permanent
negative cache (the #774 behaviour). No schema or data involved.
