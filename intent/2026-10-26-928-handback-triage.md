---
status: draft
issue: 928
author: olafkfreund
---

# Intent: the handback sender sends a valid triage block

## Problem

`apps/backend/agents/handback/send.py` posts a correction to AIFactory's
`POST /api/tasks/{task_id}/apply-correction` with only `fix_request_md`,
`source` and `confirm`. AIFactory now requires a structured `triage` block by
default (AIFactory#1872, `APP_REQUIRE_CORRECTION_TRIAGE`), so every send would
be rejected. It is latent: sending is off unless `PFACTORY_HANDBACK_SEND` is set
(`agents/handback/trigger.py`), and gitops does not set it.

## Proposed outcome

When sending is turned on, the POST body carries `confirm: true`, the fields
`ApplyCorrectionRequest` requires, and a `triage` block that passes both
AIFactory's published `handback_triage.schema.json` and its runtime
`validate_triage_report`. A contract test pins that.

## Affected users and systems

PFactory backend (handback sender), AIFactory's apply-correction route.
No deployment changes; the send gate stays off by default.

## Constraints

- Same shape as TFactory's sender and CFactory's #499 fix.
- Use the real failing-test data the request already holds; a synthetic entry only if there is none.
- No new runtime dependency; PFactory CI has no AIFactory checkout, so the contract is vendored.

## Open questions

None.
