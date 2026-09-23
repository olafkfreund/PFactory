---
status: approved
issue: 755
spec: spec/2026-09-23-755-session-split-brain.md
---

# Plan: Plan sessions are cached per process, so replicas disagree

Approved decisions (from the spec):

- New `plan_sessions` table (`session_id` PK, `tenant_id`, `seq`,
  `schema_version`, `payload` JSON, `updated_at`), NOT a column on the
  fleet-shared `job_states`. Alembic head today is `b8e1f4c7a2d9` (single
  head, verified).
- New `PlanSessionStore` beside `JobStateStore`, same sync-facing shape
  (background loop + `_run`), exposing `upsert` / `get` / `list` / `next_seq`.
  `next_seq()` allocates in one transaction, replacing the per-process
  `self._seq`.
- `PlanService` resolves the store like `_resolve_job_store()`; with a store,
  `get()` / `list_sessions()` read through it and `_save()` upserts first.
  `self._sessions` stays as the no-DB path and a write-through cache.
- One-shot idempotent import of existing `*.json` at startup, so PVC sessions
  are not stranded.
- Guard: `PFACTORY_REPLICA_COUNT > 1` with no store ⇒ ERROR naming #755;
  raises when `PFACTORY_REQUIRE_SHARED_STORE=1`. Chart injects the count.
- Out of scope: lifting `maxReplicaCount` in factory-gitops.

*Deviations (implementation, all found by running it):*

- **Id allocation is a counter table, not `max(seq)+1 FOR UPDATE`.** Postgres
  rejects `FOR UPDATE` with an aggregate (`FeatureNotSupportedError`), so the
  spec's query never ran — and the service's fallback logged a warning and
  silently used the per-process counter, so the collision #755 reports was
  still there while the tests passed. Even fixed, `max(seq)+1` races: the
  allocation and the session insert are separate transactions, so two replicas
  can read the same maximum. The migration now adds `plan_session_seq` (one
  row) and `next_seq()` is a single atomic insert-or-bump
  (`ON CONFLICT DO UPDATE ... RETURNING`), seeded from the highest existing
  `seq` so an upgraded deployment never reissues an id. Extra tests call the
  allocator DIRECTLY and assert no fallback warning — the plan's tests went
  through the service, where the failure was invisible.
- **`PlanSessionStore.close()` + negative caching.** An unready store was
  returned as `None` without closing it, leaking an event loop, a thread and a
  connection pool per `PlanService` construction. That failed
  `tests/postgres/test_p1_suite_against_postgres.py` (which builds hundreds of
  services) while every test in isolation passed. `_resolve_job_store` already
  closed its store; mine now does too, and remembers the failed URL.
- **`tenant_id` is `String(64)` NOT NULL default `'default'`**, matching the
  `job_states` convention (#308), not the nullable `String(255)` the spec
  sketched.

## Steps

1. Migration `apps/web-server/server/database/alembic/versions/` (new file,
   `down_revision = "b8e1f4c7a2d9"`): create `plan_sessions` + index on
   `tenant_id`. → verify `alembic upgrade head` in the postgres test env, and
   `tests/postgres/test_p1_alembic.py` still passes.
2. `apps/web-server/server/jobstore/` (or a sibling module): `PlanSessionStore`
   with `upsert(session_id, tenant_id, seq, payload)`, `get`, `list`,
   `next_seq`, `is_ready`. Mirror `JobStateStore`'s loop/engine handling.
   → verify by step 6's tests.
3. `apps/backend/plan/service.py`: `_resolve_session_store()` (deferred import,
   never raises, returns `None` without `DATABASE_URL`); wire `get()`,
   `list_sessions()`, `_save()`, and id allocation in `_store()`.
   → verify existing suites still pass with no `DATABASE_URL`.
4. Startup import: when a store exists and `_persist` is on, import any
   `*.json` not already present (keyed by `session_id`). Idempotent.
   → verify by step 6's import test.
5. Guard + chart: the startup check in `PlanService.__init__` (or the
   web-server startup path), and `PFACTORY_REPLICA_COUNT` injected from
   `.Values.replicaCount` in `charts/pfactory/templates/deployment.yaml`.
   → verify by unit test + `helm template` showing the env var.
6. `tests/postgres/test_plan_session_store.py` (`-m postgres`):
   a. **split-brain reproduction**: two `PlanService` instances, one store;
      A discards, B's `get()` and `list_sessions()` report `discarded`;
   b. two instances ingest concurrently ⇒ distinct session ids;
   c. startup import: JSON on disk + empty DB ⇒ rows appear; run twice ⇒ no
      change, no duplicates.
7. `tests/test_plan_service.py` (no DB): guard logs ERROR at
   `PFACTORY_REPLICA_COUNT=2`; raises with `PFACTORY_REQUIRE_SHARED_STORE=1`;
   everything else unchanged when `DATABASE_URL` is unset.
8. Negative control (not committed): point `get()` back at `self._sessions`
   while a store is set ⇒ 6a fails. Restore.
9. Read cost: time `get()` and `list_sessions()` against the postgres test DB
   with ~50 sessions; record the numbers in the PR (the cockpit polls these).

## Tests

    apps/backend/.venv/bin/pytest tests/ -q -k "plan_service or plan_persistence or emit"
    apps/backend/.venv/bin/pytest tests/postgres/ -m postgres -v
    helm template charts/pfactory | grep -A2 PFACTORY_REPLICA_COUNT

Expected: all pass; full backend suite via the pre-commit hook.

## Rollback

Revert the commit. The `plan_sessions` table is additive and unused by the
reverted code; sessions remain on disk and in `job_states` as before. Prod
stays pinned at one replica either way, so no topology depends on this.
