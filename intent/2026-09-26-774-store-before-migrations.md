---
status: draft
issue: 774
author: Olaf Krasicki-Freund
---

# Intent: a pod that migrates on boot uses the shared session store without a restart

## Problem

On the 0.6.20 deploy (2026-09-24) the first PFactory pod booted with **no
shared plan-session store**, although `DATABASE_URL` was set and the migration
creating the store's table ran moments later in the same boot:

- `plan.service.SERVICE` is created lazily the first time something imports
  it (`apps/backend/plan/service.py:2065`, PEP 562 `__getattr__`). The route
  modules import it at app import time.
- Its constructor resolves the store once (`_resolve_session_store`, line
  467). With no `plan_sessions` table yet, it closes the store and adds the
  URL to `_SESSION_STORE_UNAVAILABLE`, which the process never retries.
- Only later does the app lifespan run `init_db()` (`server/main.py:102`),
  which, with `MIGRATIONS_AUTO_APPLY=true` (the default,
  `server/config.py:58`), runs `alembic upgrade head` and creates the table.

So the pod served per-process: the table stayed empty, the on-disk sessions
were not imported, and the #758 emit lease and compare-and-set writes were
off. A manual `rollout restart` fixed it; the next boot imported 27 sessions.
Nothing surfaced this beyond one WARNING log line.

This recurs on every deploy that adds a migration the store depends on. With
more than one replica it brings back the #755 split brain until someone
notices and restarts. Today the one-replica pin hides the worst of it: the
safety features are silently off rather than the data diverging.

## Proposed outcome

- A pod that applies migrations on boot uses the shared session store from
  its first request, with no restart. The on-disk import runs once the table
  exists.
- A pod whose database genuinely has no table (an unmigrated database with
  `MIGRATIONS_AUTO_APPLY=false`) still degrades as today, loudly, and does not
  pretend to be shared.
- The behaviour is covered by a test that boots against an unmigrated
  database with auto-apply on.

## Affected users and systems

- PFactory web-server startup (`server/main.py` lifespan,
  `server/database/engine.py` `init_db`) and `plan/service.py` store
  resolution.
- Every PFactory deploy that ships a migration; operators who would otherwise
  need to know to restart.
- The one-replica pin: this is a prerequisite for lifting it safely, alongside
  `plan.replicaSignal` in factory-gitops.

## Constraints

- No change to behaviour when `DATABASE_URL` is unset (per-process, as
  today).
- Must not make startup fail when the database is briefly unreachable. The
  "degrade, never fatal" rule stays, unless
  `PFACTORY_REQUIRE_SHARED_STORE=1`, which already opts into refusal.
- Must not re-resolve the store per request in a way that builds and discards
  pools (the reason `_SESSION_STORE_UNAVAILABLE` exists).
- Out-of-band migration mode (`MIGRATIONS_AUTO_APPLY=false`, Helm Job) must
  keep working.

## Open questions

1. **Where to fix it:**
   - (a) Order: run migrations before anything constructs `SERVICE`, e.g.
     `init_db()` then an explicit service warm-up at the end of the lifespan
     hook, with the routes' import no longer triggering construction.
   - (b) Retry: stop caching "unavailable" forever. Re-check readiness
     lazily, bounded by a short back-off, so a store that becomes ready is
     picked up and the import runs then.
   - (c) Both.

   **Recommended: (a)**, plus a one-shot re-resolve right after `init_db()`
   (clear the unavailable mark and rebuild `SERVICE`'s store if it is missing).
   It targets exactly this race with no per-request cost. Bounded retry (b)
   is a wider behaviour change and can follow if a genuine transient DB
   outage at boot turns out to matter.
2. **Should a pod with `DATABASE_URL` set but no working store fail its
   readiness probe** instead of serving per-process? **Recommended:** not in
   this task. `PFACTORY_REQUIRE_SHARED_STORE=1` already gives operators that
   choice; flipping the default is a separate decision tied to lifting the
   pin.
