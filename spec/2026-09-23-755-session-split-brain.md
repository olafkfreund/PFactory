---
status: draft
issue: 755
intent: intent/2026-09-23-755-session-split-brain.md
---

# Spec: Plan sessions are cached per process, so replicas disagree

## Facts established

- A `PlanSession` already round-trips through `model_dump_json()` /
  `model_validate_json()` (`service.py:405,429`), so the payload needs no new
  serialisation to live in a database column.
- `_save()` already calls `_mirror()` (durable `job_states` row) before the
  JSON disk write, and `_resolve_job_store()` (:267) is the established pattern
  for "use Postgres when `DATABASE_URL` is set, degrade loudly when not".
- `job_states` carries lifecycle + terminal payload only — no room for the plan
  itself, and it is a fleet-shared table, so it is not the place to bolt a
  PFactory payload onto.
- DB-backed tests have a home: `tests/postgres/` with `-m postgres`, run by its
  own CI job.

## Design

### 1. A durable session store (the authoritative copy when Postgres is set)

New alembic migration adding `plan_sessions`:

| column | type | note |
| --- | --- | --- |
| `session_id` | `String(255)` PK | the existing `NNN-slug` id |
| `tenant_id` | `String(255)` null, indexed | mirrors the `job_states` column (#308) |
| `seq` | `Integer` | the numeric prefix, for atomic id allocation |
| `schema_version` | `String(8)` | same convention as `job_states` |
| `payload` | `JSON` | `PlanSession.model_dump_json()` verbatim |
| `updated_at` | `DateTime(tz)` | write time |

New `PlanSessionStore` beside `JobStateStore` (same sync-facing shape: a
background loop + `_run`, so `PlanService` stays synchronous), exposing
`upsert(session)`, `get(session_id)`, `list(tenant_id=None)` and
`next_seq()`. `next_seq()` allocates inside one transaction
(`SELECT coalesce(max(seq),0)+1 … FOR UPDATE`), replacing the per-process
`self._seq` (:378) that mints colliding ids.

### 2. `PlanService` reads through the store when it exists

- `_resolve_session_store()`, mirroring `_resolve_job_store()`: returns the
  store when `DATABASE_URL` is set and importable, else `None`.
- `get()`: store present ⇒ read the row, refresh `self._sessions[sid]` from it,
  return that. Absent ⇒ today's dict lookup.
- `list_sessions()`: store present ⇒ list from the store.
- `_save()`: store present ⇒ `upsert` first (authoritative), then the existing
  `_mirror()` + JSON disk write, unchanged.
- Ingest: store present ⇒ `next_seq()`; absent ⇒ `self._seq`.

`self._sessions` remains the single-replica path and a write-through cache; it
is never the source of truth when the store exists.

### 3. A loud guard, because the existing warning was ignored

`PFACTORY_REPLICA_COUNT` (injected by the chart from `.Values.replicaCount`)
> 1 with no session store ⇒ log **ERROR** at startup naming #755 and the
consequence ("writes will be invisible to other replicas"). With
`PFACTORY_REQUIRE_SHARED_STORE=1` it raises instead, so the gitops pin can be
lifted against a hard guarantee rather than a hope.

### Out of scope (separate, after this ships and is verified in prod)

Lifting `maxReplicaCount` in factory-gitops. This PR makes it *possible*; it
does not change prod topology.

## Alternatives rejected

- **Read-through on the PVC (issue option 2)**: the PVC is `ReadWriteOnce` on
  `local-path`, so replicas share files only while co-scheduled; a pod on
  another node gets a different directory and diverges silently (intent).
- **A payload column on `job_states`**: fleet-shared table; PFactory's plan
  payload does not belong in every service's row.
- **Keeping the pin forever (intent Q1b)**: leaves a permanent guard around a
  known-broken store and blocks RFC-0016's own direction.
- **Redis/pub-sub invalidation**: new infrastructure for a cache that Postgres
  removes the need for.

## Risks

- **Read cost.** `get()`/`list_sessions()` become queries; the cockpit polls
  them. Mitigation: payloads are small (JSON plans), one indexed PK lookup;
  measured in the plan before the pin is lifted.
- **Migration.** Sessions already on the PVC are not copied by this change;
  with `DATABASE_URL` set the store starts empty. The plan adds a one-shot
  import of existing `*.json` files at startup (idempotent, by `session_id`),
  so nothing is lost.
- **Two writers, last-write-wins.** The upsert overwrites the row; concurrent
  edits to one session can still lose a field. Out of scope here (it needs the
  optimistic-locking story RFC-0016 sketches), and no worse than today.

## Verification

- **The split-brain reproduction, as a test**: two `PlanService` instances over
  one store; A discards a session, B's `get()`/`list_sessions()` must report
  `discarded` without restarting. Fails on `dev` today.
- Id allocation: two instances ingest concurrently ⇒ distinct `session_id`s.
- Startup import: a store dir with existing JSON + an empty DB ⇒ rows appear,
  and running it twice changes nothing.
- Guard: `PFACTORY_REPLICA_COUNT=2` with no store ⇒ ERROR logged;
  `+PFACTORY_REQUIRE_SHARED_STORE=1` ⇒ startup raises.
- Degradation: with `DATABASE_URL` unset every existing test still passes (the
  JSON path is untouched).
- Negative control: point `get()` back at the dict while the store is set ⇒ the
  split-brain test fails again.
