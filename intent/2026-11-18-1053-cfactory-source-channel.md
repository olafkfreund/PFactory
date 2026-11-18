---
status: draft
issue: 1053
author: olafkfreund
---

# Intent: accept CFactory as a planning source channel

## Problem

CFactory sends `POST /api/plan/sessions/ingest-text` with `"channel": "cfactory"`
(CFactory `apps/backend/cfactory/card_intake.py:276`). PFactory types
`SourceChannel = Literal["portal","cli","mcp","github_issue","github_discussion","agent"]`
(`apps/backend/plan/models.py:24`), so pydantic rejects the value. The route
(`apps/web-server/server/routes/plan_pipeline.py`, `ingest_text`) turns that
`ValueError` into HTTP 400 `invalid request` and logs nothing, so every CFactory
planning dispatch fails undiagnosably (since 2026-09). Verified still true on `dev` (cbe26289).

## Proposed outcome

- ingest-text accepts `channel: "cfactory"`; the session records `source_channel="cfactory"`.
- An unknown channel still returns 400 with the sanitised detail.
- The 400 cause is logged server-side (no body, no tokens) so the next one is diagnosable.

## Affected users and systems

CFactory planning dispatch (hard cards); PFactory plan ingest API. CFactory unchanged.

## Constraints

- Additive only; response detail stays sanitised.
- CFactory is an authenticated operator surface, so its content trust stays `trusted`
  (same as portal/agent), not `untrusted_user_content`.
- Low and medium cards still route straight to AIFactory by default, so a live board run
  does not exercise this path.

## Open questions

None. Decision (owner delegation 2026-11-18): add `cfactory` to PFactory (the issue's first
option; CFactory already sends it and is a vendored-contract consumer).
