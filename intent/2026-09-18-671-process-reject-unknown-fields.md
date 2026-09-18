---
status: draft
issue: 671
author: Olaf Krasicki-Freund
---

# Intent: `/process` silently drops `repo`/`base_ref`

## Problem

`POST /api/plan/sessions/{id}/process` takes an optional `PlanUpdateBody`
(`apps/web-server/server/routes/plan_pipeline.py:288`) with `title`,
`description`, `criteria`. Pydantic's default ignores unknown fields, so a
caller who sends `{"repo": ..., "base_ref": ...}` gets HTTP 200 and a complete,
confident **greenfield** plan — no reconnaissance, no constitution, nine
readiness checks `not_applicable` with plausible reasons. `repo`/`base_ref`
belong on `/ingest-text`. Nothing tells the caller the parameter was
discarded. Cost one misleading customer-demo run.

## Proposed outcome

- `/process` with a field it does not understand returns **422** naming the
  field, instead of 200 and an ungrounded plan.
- A bare `POST /process` and one with valid edit fields behave exactly as now.
- Other request bodies in `plan_pipeline.py` get the same treatment where it
  is safe (issue asks to check sibling routes).

## Affected users and systems

- API/MCP/portal callers of the plan-session routes.
- `apps/web-server/server/routes/plan_pipeline.py` (12 `BaseModel` bodies).
- Frontend `apps/frontend-web` — must be checked that it sends no extra keys
  today, or the change breaks the portal.

## Constraints

- Must not break the portal or the MCP tools: any client currently sending
  extra keys must be found and fixed in the same change.
- Must not add late-binding repo support here (the issue allows either; the
  422 is the smaller, safe one).

## Open questions

1. Scope: forbid unknown fields on `PlanUpdateBody` only (the reported case),
   or on all 12 request bodies in `plan_pipeline.py`? Recommendation: all of
   them, after grepping each client — the silent-drop hazard is identical.
