---
status: approved
issue: 758
spec: spec/2026-09-23-758-durable-emit-lock.md
---

# Plan: one emit per plan session, and no lost session writes, across replicas

Self-contained summary of the approved decisions:

- **Emit lease** on `plan_sessions`: columns `emit_lease_owner VARCHAR(128)`
  and `emit_lease_until TIMESTAMPTZ`, both nullable.
  - Acquire is one atomic `UPDATE ... WHERE lease is free or expired
    RETURNING`; release clears the lease only if the caller owns it.
  - TTL comes from `PFACTORY_EMIT_LEASE_TTL_SECONDS` (default 1800; an invalid
    or non-positive value falls back to it).
  - Taken in `PlanService._emit_lock` after the in-process lock, for live
    emits only; dry runs skip it.
  - Once the lease is held, the session is re-read; if `emitted_issue_number`
    is already set **and** the status is `emitted`, the emit returns without
    emitting. (Deviation, see step 4: `emitted_issue_number` alone is also set
    by a partial emit, which must still resume under #119. The contract emit
    takes the lease but has no re-check: a set issue number is its normal
    precondition.)
  - Failing to acquire, or the store being unreachable, refuses the emit
    (fail closed) with `EmitInProgressError` (409).
- **Lost-update guard:** a `version INTEGER NOT NULL DEFAULT 0` column.
  - `upsert` becomes a compare-and-set on `expected_version`; `get()` returns
    `(payload, version)`.
  - `PlanService` tracks the version on each session copy as a pydantic
    `PrivateAttr` (`_store_version`), which is not a field and never
    serialised, so payloads and the JSON mirror are unchanged. (Deviation from
    the approved `dict[str, int]`: a dict keyed by session id shares one
    version between two copies in the same process, so a long `process()`
    could overwrite a human's approve on the same pod, which is the only
    deployed shape while the KEDA pin is 1.)
  - A conflict raises `StaleSessionError` (409) and refreshes the cached copy.
- **No store** (`DATABASE_URL` unset): behaviour unchanged.
- Id allocation needs nothing more; #767 fixes the counter seed.
- Both errors are `PlanServiceError` subclasses. The emit routes map them to
  409 before the generic 400.

Work happens in the worktree
`/tmp/.../scratchpad/pf-758` on `fix/758-durable-emit-lock`, never in the
shared PFactory checkout (see the agent-bus note of 2026-09-24).

## Sequencing

#767 (open) also edits `apps/web-server/server/jobstore/plan_session_store.py`
and `tests/postgres/test_plan_session_store.py`. **Wait for #767 to merge
into `dev`, then rebase this branch on `origin/dev`** before step 1, so the
counter fix and this change do not conflict. If #767 has not merged when this
plan is approved, stop and report rather than build on top of an unmerged PR.

## Steps

1. **Migration** `apps/web-server/server/database/alembic/versions/2026MMDD_<rev>_plan_session_lease_version.py`,
   with `down_revision` set to the head at rebase time (currently
   `c9f2a6d1e483`, unless #767 adds one). It adds the three columns, all
   nullable or defaulted, and a downgrade that drops them.
   → verify with `alembic upgrade head` then `downgrade -1` then
   `upgrade head` against a local Postgres
   (`docker run -e POSTGRES_PASSWORD=... postgres:<ci version>`, the same URL
   shape as `ci.yml`'s `TEST_POSTGRES_URL`); `tests/postgres/test_p1_alembic.py`
   stays green.

2. **Tests first:**
   - `tests/postgres/test_plan_session_store.py`: lease acquire, re-acquire
     (False), wrong-owner release (still held), expired (acquirable); CAS
     upsert with a stale version (`None`, row unchanged) and a matching
     version (increments).
   - `tests/test_plan_emit_lease.py`: service level with a fake store; a held
     lease gives `EmitInProgressError` and no GitHub call; a re-check with
     `emitted_issue_number` set gives no emit; dry run takes no lease; a store
     outage refuses the emit.
   - `tests/test_plan_stale_write.py`: a stale `_save` raises
     `StaleSessionError`, other store errors are still swallowed, and the
     cache is refreshed after a conflict.
   - `tests/postgres/test_two_process_emit.py`: two `multiprocessing` workers
     emit one session against the Postgres test DB with a recording fake
     GitHub. Exactly one epic is created, and the other worker gets
     `EmitInProgressError`. A second case: replica A approves while replica B
     holds an older copy; the approval survives and B gets
     `StaleSessionError`.

   → verify by running them on the current code: all fail (missing API or
   columns), and nothing else breaks.

3. **Store** (`plan_session_store.py`): `acquire_emit_lease`,
   `release_emit_lease`, CAS `upsert(..., expected_version)` returning the new
   version or `None`, and `get()` returning `(payload, version)`. All go
   through the existing `_run()`. Update the `SessionStore` protocol in
   `apps/backend/plan/service.py` to match.
   → verify with the step 2 store tests.

4. **Service** (`apps/backend/plan/service.py`):
   - `_store_version` is set wherever a session is loaded from the store
     (`_load_from_store`) or written to it (`_upsert_session`, the import).
     A new session starts at 0 (insert). A copy with no known version (from
     `list_payloads`) reads it with `get()` just before writing.
   - `_upsert_session` passes the expected version. On `None` it refreshes
     from the store and raises `StaleSessionError`.
   - **`_save` keeps its "never raises" contract for everything except
     `StaleSessionError`**, which it lets propagate. That carve-out is
     documented in the docstring.
   - The import path (`_import_sessions_into_store`) inserts with expected version 0.
   - `_emit_lock` gets the lease acquire, re-check and release in `finally`,
     plus the TTL setting.
   - New `EmitInProgressError` and `StaleSessionError(PlanServiceError)`.

   → verify with the step 2 service tests and the existing emit, persistence
   and concurrency tests (`-k "plan_service or plan_persistence or emit or
   concurrency"`).

5. **Routes** (`apps/web-server/server/routes/plan_pipeline.py`): in `emit`
   and `emit_contract`, `except (EmitInProgressError, StaleSessionError)`
   answers 409 before the generic `PlanServiceError` 400. Other routes that
   save (approve, discard, reject, edit) also map `StaleSessionError` to 409.
   Grep for the `except PlanServiceError` sites. `mcp_rpc.py:243` is a
   dry-run contract emit and needs no lease, but maps `StaleSessionError` if
   it can surface there.
   → verify with a route test: 409 for both errors, 400 still for others.

6. **Docs:** emit route docstrings (regenerate the OpenAPI if PFactory has a
   drift gate like AIFactory's; check `techdocs.yml`), and the operator
   guide's replica/HA section. Cover: emits are safe across replicas; 409
   meanings; `PFACTORY_EMIT_LEASE_TTL_SECONDS` with its default and what
   happens when unset; the one-replica pin can be lifted only after this and
   #767 are released and migrated.
   → verify by grep for the setting in the docs.

7. **Gates:** full PFactory suites as `ci.yml` runs them (including the
   Postgres job against the local container), ruff and mypy on the changed
   files, and the repo's ratchet.
   → verify all green locally.

8. **PR** to `dev` linking this intent, spec and plan, with `Fixes #758`.
   Merge when CI is green.
   → verify CI green.

9. **Release and rollout** (after #767 is also on `dev`): bump the version on
   `dev` (the #759 pattern), sync `dev -> main` (#760 or its successor), and
   wait for deploy. Then run `alembic upgrade head` in the prod pod (prod is
   at `b8e1f4c7a2d9` today) and confirm `alembic current` shows the new head.
   **Do not change the KEDA pin here**; lifting it is a separate, explicit
   decision.
   → verify: the prod image shows the new SHA; `alembic current` is at head;
   one live emit in the demo project succeeds and creates exactly one epic;
   a second concurrent click gets 409.

## Tests

```bash
docker run -d --rm --name pf-pg -e POSTGRES_USER=pfactory_test -e POSTGRES_PASSWORD=pfactory_test -e POSTGRES_DB=pfactory_test -p 5432:5432 postgres:<ci version>
export TEST_POSTGRES_URL="postgresql+asyncpg://pfactory_test:pfactory_test@localhost:5432/pfactory_test"
apps/backend/.venv/bin/pytest tests/postgres -q
apps/backend/.venv/bin/pytest tests -q -k "emit or plan_service or plan_persistence or concurrency or stale"
# then the full ci.yml invocations
```

## Rollback

- **Code:** revert the fix commit on `dev` and cut a patch release.
- **Schema:** the three columns are additive and ignored by older code, so a
  code rollback needs no downgrade. `alembic downgrade -1` drops them if
  wanted.
- **Operations:** the KEDA pin stays at 1 throughout, so a rollback cannot
  reintroduce multi-replica emits.
