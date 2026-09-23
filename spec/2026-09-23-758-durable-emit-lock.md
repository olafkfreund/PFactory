---
status: draft
issue: 758
intent: intent/2026-09-23-758-durable-emit-lock.md
---

# Spec: one emit per plan session, across every replica

Decisions carried from the approved intent:

- A concurrent second emit of the same session fails fast with **409**.
- Lock mechanism is settled here: the intent leaned towards an advisory lock.
  This spec chooses a lease row instead, for the reason given under Design.
- Id allocation: confirmed there is no per-process dependency (see below).
- `DATABASE_URL` unset: behaviour unchanged (in-process lock only).

## Design

### Id allocation (intent question 3): already durable

`PlanService._next_seq()` (`apps/backend/plan/service.py:736`) calls
`store.next_seq()` when a store exists. `PlanSessionStore.next_seq()`
(`apps/web-server/server/jobstore/plan_session_store.py:130`) allocates with
one `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` on the
`PlanSessionCounter` row, which is atomic across replicas. `_store_lock` only
guards the in-process dict and file mirror, and is correct per process. No
change.

### The emit lease

A lease on the session row in the shared store, taken atomically and released
explicitly. It expires on its own if the holder dies.

- **Schema:** an Alembic migration adds two nullable columns to
  `plan_sessions` (`PlanSessionRow`, `plan_session_models.py`):
  `emit_lease_owner VARCHAR(128)` and `emit_lease_until TIMESTAMPTZ`. Nullable
  with no default, so existing rows and the mirror are unaffected.
- **Store API** (`PlanSessionStore`, and added to the `SessionStore`
  protocol):
  - `acquire_emit_lease(session_id, owner, ttl_seconds) -> bool` runs one
    statement:
    `UPDATE plan_sessions SET emit_lease_owner=:owner, emit_lease_until=now()+:ttl
    WHERE session_id=:id AND (emit_lease_until IS NULL OR emit_lease_until < now())
    RETURNING session_id`. It returns True if a row came back. That single
    atomic statement is the whole lock: two replicas cannot both match.
  - `release_emit_lease(session_id, owner) -> None` clears both columns
    `WHERE session_id=:id AND emit_lease_owner=:owner`, so it never releases
    someone else's lease.
  - Both run through the store's existing `_run()` (a dedicated loop thread),
    like `upsert` and `next_seq`. No connection is held between calls.
- **`PlanService._emit_lock(session_id)`** (around line 1598) becomes:
  1. Take the in-process lock, as today (it still protects the threads of one
     process).
  2. If a store exists: `owner = f"{hostname}:{pid}:{uuid4().hex[:8]}"` and
     `acquire_emit_lease(session_id, owner, ttl=EMIT_LEASE_TTL_SECONDS)`. On
     False, raise a new `EmitInProgressError(PlanServiceError)`: "an emit of
     <session> is already running on another replica".
  3. Once the lease is held, re-read the session from the store (#757 `get()`
     reads the store). If `emitted_issue_number` is already set, the other
     replica finished first, so return without emitting (today's in-process
     double-click behaviour, now across replicas).
  4. In `finally`: `release_emit_lease`, best-effort and logged on failure. The
     TTL covers a crash.
- **Store failure while acquiring** (DB unreachable) fails closed: raise
  `EmitInProgressError`-style refusal "cannot confirm no concurrent emit", and
  never emit unguarded. An emit is rare and human-initiated, so a refused emit
  costs a retry. A duplicate epic costs a manual cleanup of dozens of issues.
- **`EMIT_LEASE_TTL_SECONDS`**: env `PFACTORY_EMIT_LEASE_TTL_SECONDS`, default
  1800 (30 min). Longer than any observed live emit (#725: minutes), short
  enough that a crashed pod does not block a session for long. Invalid or
  non-positive values fall back to the default.
- **Dry runs** (`dry_run=True`) create nothing on the target repo, so they
  take only the in-process lock, not the lease. A preview is never refused
  because a real emit is running elsewhere.

### HTTP mapping

`routes/plan_pipeline.py` `emit` and `emit_contract` catch
`EmitInProgressError` before the generic `PlanServiceError` and answer **409**
with the message. Other `PlanServiceError`s keep their 400. Any other caller of
`SERVICE.emit*` (MCP tools; grep during planning) gets the same mapping.

### Docs

The PFactory emit endpoint docstrings (feeding the generated OpenAPI, if the
repo has a drift gate like AIFactory's) and the operator guide section on
replicas: emits are safe across replicas once this ships, 409 means another
emit is running, and the TTL setting, with its default and what happens when
unset.

## Alternatives rejected

- **Postgres session-level advisory lock** (`pg_try_advisory_lock`). It is
  released on disconnect (good), but it has to be held on one pooled
  connection for a multi-minute emit that runs in a worker thread, while the
  store drives an async engine on its own loop thread. That means pinning a
  connection across threads and loops for the whole emit. The lease needs no
  held connection.
- **`SELECT ... FOR UPDATE` on the session row** (as `_durable_admit` does).
  The admit is a short transaction; here the transaction would stay open for
  the entire emit, with the same held-connection problem plus a long-running
  transaction.
- **Idempotent emit** (check GitHub for an existing epic before creating one).
  It is racy on its own, since both replicas can check before either creates.
  It is a useful second line but not a lock.

## Risks

- **A lease expires during a very slow emit** (longer than the TTL). Another
  replica could then start. Mitigated by the 30-minute default, well above
  observed emits, and by step 3's re-check. Not fully eliminated; a heartbeat
  renewal is possible later if emits approach the TTL.
- **Clock:** expiry uses the database's `now()` on both sides, so pod clock
  skew does not matter.
- **Migration:** additive nullable columns, safe on a live table. If it has not
  run, `acquire` fails, and the fail-closed rule means emits are refused until
  it does. The deploy runs migrations first, which must be confirmed in the
  plan.

## Verification

- **Unit** (store on the Postgres test env, as `tests/postgres/` does):
  - acquire, acquire again: False;
  - release by the wrong owner: still held;
  - expired lease: acquirable again.
- **Service:**
  - with a store, a held lease makes `emit` raise `EmitInProgressError`, and no
    GitHub call is recorded;
  - once the lease is acquired and `emitted_issue_number` is set, `emit`
    returns without emitting;
  - `dry_run` never touches the lease;
  - with no store, behaviour is identical to today (existing emit tests
    unchanged).
- **Two processes, not threads** (the test the #755 comment asked for): two
  `multiprocessing` workers against the Postgres test DB emit one session with
  a fake GitHub that records epic creation. Exactly one epic is created, and
  the other worker gets `EmitInProgressError`.
- **Route:** `EmitInProgressError` maps to 409; other errors stay 400.
- Full PFactory suites and its CI gates green.
