---
status: approved
issue: 774
intent: intent/2026-09-26-774-store-resolve-before-migrations.md
---

# Spec: The first boot after a migration runs per-process, silently

## Facts established

- `plan_pipeline.py:31` imports `SERVICE` at module scope, so `PlanService()`
  is built while `main.py` imports routers — before the lifespan's `init_db()`
  applies migrations. Prod timings in #774: warning at `:17.097`, migration at
  `:17.601`.
- `self._session_store` is read at five call sites in `service.py`, now
  including the #758 emit lease and the #766 compare-and-set path. A single
  resolution point therefore fixes all of them.
- Tests (mine included) assign `pod._session_store = <fake>`, so whatever
  replaces the attribute must stay assignable.
- The permanent negative cache exists to stop a real leak: each
  `PlanSessionStore` owns an event loop, a thread and a pool, and an unready one
  was previously rebuilt per `PlanService`.

## Design

**Retry with a cooldown, not ordering** (intent Q1).

1. `_SESSION_STORE_UNAVAILABLE: set[str]` becomes
   `_SESSION_STORE_RETRY_AFTER: dict[str, float]` — a monotonic deadline per
   URL. `_resolve_session_store()` returns `None` without attempting anything
   while the deadline is in the future, and sets
   `now + _STORE_RETRY_COOLDOWN_SECONDS` (30s) on every failure. So a genuinely
   unreachable DB is attempted at most twice a minute per process, which keeps
   the leak fix intact: an unready store is still closed immediately.
2. `PlanService._session_store` becomes a **property** over
   `self.__store`, with a setter so existing assignments (tests, `__init__`)
   keep working. The getter returns the cached store, else calls
   `_resolve_session_store()`; on a first success it:
   - caches the store,
   - runs `_import_sessions_into_store()` (the one-shot on-disk import, which is
     already idempotent by `session_id`),
   - logs at INFO that the store was picked up after boot,
   - re-runs `_warn_if_multi_replica_without_store` so the ERROR stops once the
     store exists.
   An explicitly injected store (constructor arg) is never re-resolved.
3. `__init__` no longer resolves eagerly: it stores the injected value or
   `None`, and the first use resolves. This is what removes the boot-order
   dependency — nothing needs to know when migrations run.
4. The per-process state stays visible: each failed resolution attempt (at most
   one per cooldown) logs the existing WARNING, so a pod that never gets a store
   keeps saying so rather than falling silent after one line.

## Alternatives rejected

- **Order migrations before the route import** — fixes this deploy and leaves
  an unenforced rule ("never import `SERVICE` before startup"); the next import
  move re-creates the failure silently (intent Q1).
- **Resolve on every call with no cooldown** — reinstates the leak the #755
  review caught.
- **A background thread that polls for the table** — a thread per process to
  replace a lazily-evaluated property; more machinery, same result.
- **Drop the negative cache entirely and rely on `is_ready()`** — `is_ready()`
  is the expensive part (it builds the store first), so this is the leak again.

## Risks

- A read during the cooldown window uses the per-process path, so a session
  created in the first seconds of a pod's life may be written per-process and
  only appear in the store on its next `_save`. The on-disk import covers the
  pre-existing ones; a session created inside the window is picked up by its
  next transition. Worth stating in the PR rather than hiding.
- The property is called on hot paths (`get`, `list_sessions`); when a store is
  cached it is one attribute read plus a `None` check, and when it is not, a
  dict lookup against the deadline. No DB work in the common case.

## Verification

Against a real Postgres, calling the primitives directly (the lesson from the
#755 review — my previous tests went through layers that hid the failure):

- **The #774 reproduction**: construct `PlanService` against a DB with no
  `plan_sessions` table; assert it is per-process; create the table; assert the
  *same instance* now uses the store, has imported the on-disk sessions, and
  logged the pickup — with no restart and no new instance.
- **The leak guard still holds**: with an unreachable URL, N resolution attempts
  in a row build at most one store and close it; a second attempt inside the
  cooldown builds none.
- **After the cooldown** a further attempt is made (fake clock, not a sleep).
- **No `DATABASE_URL`** ⇒ no attempt, no warning, unchanged behaviour.
- **An injected store** is never re-resolved.
- Negative control: pin the cooldown to infinity (the old permanent cache) ⇒ the
  reproduction fails.
