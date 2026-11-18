---
status: draft
issue: 1074
author: olafkfreund
---

# Intent: fill originator, agent identity and autonomy tier in the emitted plan signature

Refs olafkfreund/Factory#1958 (acceptance item 3), follows #996 and #1011.

## Problem

The first live schema-2 signature (plan session 219, emitted to olafkfreund/aifactory-demo#605) has `originator: null`, `agent_identity: null` and `autonomy_tier: "unknown"`. #996 stamps the two identities at intake, but only from the authenticated principal. A session that arrives through a service principal (a CFactory card), the in-process MCP/agent lane (`plan/agent_api.py`), or a portal login as the default user has no user id and no `acw_` key id, so both stay null. `autonomy_tier` is copied only from `plan.autonomy_tier`, which is set by the `factory:*` label, a migration, or a flagged injection scan; an unlabelled plan emits `unknown` even though the signature already holds a `risk_class`. So Factory#1958 cannot answer "agent- or human-authored, at what autonomy tier".

## Proposed outcome

- Every emitted signature names who originated the session: the user id when known, otherwise the intake lane (`channel:<source_channel>`, e.g. `channel:cfactory`, `channel:agent`, `channel:portal`).
- Every signature names the agent that drafted the plan: the `agent:pfactory/<key id>` identity when an `acw_` key was used, otherwise PFactory's own planner (`agent:pfactory/planner`), in the same format.
- `autonomy_tier` is the routed tier when set, otherwise derived from the deployment `risk_class` (low, medium, high map to low, medium, hard), otherwise `unknown`.
- Each filled value says where it came from, in additive `*_basis` keys next to the value (the `risk_class_basis` pattern), so a reader can tell a known principal from a lane fallback, and a routed tier from a risk-derived one. Review (Codex) found that a fallback value changes what the bare field means; the basis keys keep that distinguishable without a schema bump.
- Schema 2 stays valid for old rows (nullable); `python -m plan.governance_cli` prints the new values.

## Affected users and systems

PFactory plan-docs emit (`render.py`, `service_helpers.py`, `plan_pipeline.py`), readers of the run signature (`governance_cli`, Factory#1958/#1961).

## Constraints

- No second identity helper: the `agent:pfactory/<id>` format is defined once and reused by `_attribution`.
- The signature format is PFactory's own (not a hub schema), so additive keys need no hub change and no re-vendor. Existing keys keep their names; schema stays 2, old rows stay valid (a missing basis key means the old meaning).
- The intake rule from #996 and the completion event (Factory#1960) stay unchanged: session fields remain null when unknown, the fallback is applied when the signature is built.
- The `channel:` value is a lane label, not a person; it never contains an email or credential.

## Open questions

None. Owner delegation 2026-11-18: take the recommended option. Decision: the fallback lives in the signature builder, not at intake, because #996 and the completion tests require honest nulls at intake.
