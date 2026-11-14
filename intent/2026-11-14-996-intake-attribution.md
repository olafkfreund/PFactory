---
status: draft
issue: 996
author: olafkfreund
---

# Intent: record who started a plan session, and emit it in the run signature

Epic: olafkfreund/Factory#1960. Parent intent: Factory `intent/2026-10-26-1960-agent-git-identity.md` (slice 2, "capture the originator at intake"). AIFactory did its half in AIFactory#1897.

## Problem

PFactory's run signature has the fields `originator` and `agent_identity` (#848, Factory#1961), and both are always null: `apps/backend/plan/emit/docs/render.py` writes `None` for each, unconditionally. So no emitted plan can answer "who started this, and was it an agent".

The value is available and dropped. Every plan ingest route requires an authenticated caller (`require_plan_access("member")`), and the MCP proxy routes call the same handlers with the key's principal set. But `SERVICE.ingest_text` and `ingest_bytes` take no actor, and `PlanSession` has no field to hold one.

## Proposed outcome

- A plan session records, at intake, the human who started it (`originator`) and the `acw_` key it came in on (`agent_identity`), each null when unknown.
- The run signature emits those two values instead of a constant null.
- The values use the same rules as AIFactory#1897, so the two services agree: `originator` is the principal's user id (null for a service principal or the default user), and `agent_identity` is `agent:pfactory/<api_key_id>` when the caller used an `acw_` key.
- Nothing is invented: a session ingested by a service token, or before this change, emits null for both.

## Affected users and systems

PFactory only: the plan ingest routes, `PlanService`, `PlanSession`, the run signature. Readers of the signature (`governance_cli`, Factory#1961 consumers) see real values where they saw null.

## Constraints

- Null when unknown; never a placeholder or a default.
- No change to `auth.py`, the WebSocket auth or the key model (team t312 owns PF#991 there).
- No change to the `FromIssueBody` contract in this slice: AIFactory's poller calls from-issue with a service token, so that lane records null until the issue author is sent (a cross-repo change, tracked separately).
- Old persisted sessions must still load.
- The value is attribution, not proof: it is only as strong as the credential the caller used.

## Open questions

None. The `human:<provider>/<subject>` prefix from the epic is left as AIFactory left it (raw user id now, a pure formatting change later, done in both services together).
