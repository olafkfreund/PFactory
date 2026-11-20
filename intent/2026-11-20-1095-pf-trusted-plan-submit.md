---
status: draft
issue: 1095
author: olafkfreund
---

# Intent: trusted plan submit from an agent, with no silent re-plan

## Problem

A plan written and approved in Claude, Codex or agy can reach PFactory over MCP (`plan_ingest`, `plan_process`, `plan_approve`) but cannot be emitted to AIFactory over MCP: `plan_emit` does not exist, so the handover needs curl with the shared admin token. `plan_approve` records whatever `approver` string the caller sends, so an approval says nothing about who approved. When the live emit to AIFactory `/api/tasks/from-plan` fails or times out, PFactory silently starts an ordinary create-and-run build that re-plans the work, and the only trace is `fallback: true`.

## Proposed outcome

- An agent can emit an approved plan over MCP (`plan_emit`), dry-run by default.
- Every approval records the authenticated principal server-side (user id, or "shared token"); the free-text approver stays a display name.
- A live emit over MCP is refused unless the session was approved by a resolved user principal.
- A failed or timed-out emit returns an error with AIFactory's reason; no automatic re-planned build. The old fallback stays available behind an explicit opt-in flag.
- Repeating a live emit of the same contract does not start a second build.

## Affected users and systems

PFactory MCP tools (`plan_control.py`), plan pipeline routes (`approve`, `emit-contract`), plan service and contract emit. Users of `parr-run` and the Claude/Codex/agy MCP config. AIFactory `/api/tasks/from-plan` is called the same way.

## Constraints

- The existing REST approve/emit behaviour for the shared token keeps working (`parr-run` uses it); only the new MCP emit path adds the user-principal rule.
- No new dependency; reuse `request.state.user_id` and the audit log.
- Part of Factory#3864 (P1, W0).

## Open questions

None. Owner pre-approved intent/spec/plan unseen for this programme (2026-11-20).
