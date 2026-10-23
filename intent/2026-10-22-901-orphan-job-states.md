---
status: approved
issue: 901
author: olafkfreund
---

# Intent: the stale-ingest sweep cannot end orphaned `job_states` rows

## Problem

`discard_stale_ingested()` (`apps/backend/plan/service.py`, #808) walks only
`plan_sessions`. A `job_states` row whose session was lost (for example one
ingested before #755 made sessions shared) has no session to walk, so it stays
`queued` forever and KEDA counts it as pending work. Two such rows were ended by
hand in production on 2026-10-22 (#808).

## Proposed outcome

The same hourly sweep also ends `pfactory` `job_states` rows that are `queued`
(native `ingested`), not ended, older than the same TTL, and have no
`plan_sessions` row. They become terminal with a reason that names the orphaned
session, and the sweep logs how many it ended.

## Affected users and systems

PFactory web-server background sweep, the `job_states` table, the KEDA query
that counts queued pfactory rows.

## Constraints

- Must not touch a row whose session still exists, a fresh row, or any row that
  is not `queued`.
- Must work with the durable store (Postgres in CI/production, SQLite in unit
  tests) and be a no-op on the in-memory path (no `job_states` there).
- Terminal state follows the existing native map (`discarded`).

## Open questions

None.
