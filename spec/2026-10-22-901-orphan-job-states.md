---
status: approved
issue: 901
intent: intent/2026-10-22-901-orphan-job-states.md
---

# Spec: end orphaned `job_states` rows in the stale-ingest sweep

## Design

Decision (owner, #808/#901): PFactory gives abandoned ingested work a terminal
path itself (option B); this extends it to rows with no session.

1. `JobStateStore.end_orphaned_queued(older_than, live_ids) -> int`
   (`apps/web-server/server/jobstore/store.py`). In one transaction: select
   `job_id`s where `service='pfactory'`, `lifecycle_state='queued'`,
   `ended_at IS NULL`, `created_at < older_than`; drop those in `live_ids`;
   then a conditional `UPDATE ... WHERE job_id IN (...)` with the same
   predicate (so a row that moved on meanwhile is not touched) setting
   `service_status='discarded'`, `lifecycle_state=lifecycle_state_for('discarded')`
   (`failed` today), `ended_at=now`, and `error` naming the orphaned session.
   Plain SQLAlchemy, so it runs on Postgres and SQLite alike.
2. `PlanService.discard_stale_ingested()` keeps its session walk, then calls
   the store method (when the store has it) with `now - TTL` and the ids of the
   sessions it just listed (the `plan_sessions` rows). It logs
   `stale ingest sweep ended %d orphaned job_states row(s)` and never raises.
   The return value (discarded session ids) is unchanged.

The terminal mapping is the job-store one in `server/jobstore/lifecycle.py`
(`discarded -> failed`), not the board column in `service_helpers.py`
(`discarded -> done`, a kanban column). Using `lifecycle_state_for` keeps the
two in step with `upsert`.

## Alternatives rejected

- SQL `NOT EXISTS` against `plan_sessions`: couples the job store to the
  session table and breaks when the session store is in-memory but
  `job_states` is durable.
- `NOT IN (<all session ids>)` in one UPDATE: unbounded parameter list
  (asyncpg caps at 32767). Selecting the few stale candidates first is bounded.
- Option A (fix the KEDA metric): owned by factory-gitops#273.

## Risks

- If the shared session list fails, `_all_sessions` falls back to the local
  cache, so a live session could look orphaned. Bounded by the TTL: a queued
  row older than the TTL belongs to an ingested session the session pass
  discards anyway.

## Verification

New tests (SQLite store, run in the backend CI job and the Postgres suite):
an orphan older than the TTL is ended with the reason; a fresh orphan, a row
with a live session, and non-queued rows are untouched. `ruff`, the mypy
ratchet, and the existing `tests/test_plan_discard.py` stay green.
