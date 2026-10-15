---
status: draft
issue: 862
author: olafkfreund
---

# Intent: a plan session stays processing/ingested when process() raises

## Problem

`PlanService.process()` (`apps/backend/plan/service.py:1167-1214`) sets
`session.status = "processing"` (`:1188`) and `"reviewing"` in memory only and
saves once, at the end, as `processed` (`:1207`). If any stage raises (for
example the ValueError after a GitHub PR review ingest,
`web-server/server/routes/github.py:551-560`), nothing is saved:

- with no session store, the cached session object keeps `processing`;
- with a session store, other readers see the last saved `ingested`.

The only failure record is the durable `job_states` row written by
`process_async` (`service.py:1369-1383`), and no status endpoint reads it.

Readers: `GET /api/plan/sessions/{id}` (`routes/plan_pipeline.py:283`), the
board, and MCP `plan_status` (`agents/tools_pkg/tools/plan_control.py:191`)
show an in-flight or not-started session forever.

## Proposed outcome

When `process()` raises, the session is saved with a terminal `failed` status
that every reader shows (API, board, MCP), and the user can re-run processing.

## Affected users and systems

PFactory planning engine (`plan/service.py`), the portal planning board and
session detail, MCP `plan_status`.

## Constraints

- The caller still gets the original exception (contract unchanged).
- Exception detail must not reach API callers (repo rule, `server/error_ref.py`).
- A stale-session conflict (#758) must not overwrite another replica's change.

## Open questions

None.
