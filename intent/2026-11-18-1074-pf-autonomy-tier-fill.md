---
status: draft
issue: 1084
author: olafkfreund
---

# Intent: fill autonomy_tier in the plan signature when no deployment block exists

Follow-up to #1074 (fixed by #1076). Refs olafkfreund/Factory#1958 item 3.

## Problem

Live session 232 (level 7 notes-api, emitted to olafkfreund/aifactory-demo#611) has `originator` and `agent_identity` filled, but `autonomy_tier` is `unknown` (basis `absent`) and `risk_class` is `unknown`. `resolve_tier` (`plan/service_helpers.py`) buckets the `risk_class` read from `session.epic.deployment`. The deployment builder (`plan/feasibility/deployment.py:242`) returns `None` for a greenfield plan with no CI, deploy manifest, or named PaaS target, so there is no block, no `risk_class`, and the tier falls through to `unknown` in a signed row.

## Proposed outcome

- An emitted signature never carries `autonomy_tier: "unknown"` from the resolver's own fall-through (a routed tier is passed through unchanged). With no routed tier and no risk class it carries the conservative tier `hard` with `autonomy_tier_basis: "default-conservative"`.
- `risk_class` stays `unknown`/`absent` (the deployment surface is absent or was not assessed); it is not invented.
- Schema stays 2; the `*_basis` convention is kept; old rows stay valid.

The fallback records the tier in the signature; it does not itself change routing.

## Affected users and systems

`plan/service_helpers.py`, `plan/emit/docs/render.py`, readers of the signature (`governance_cli`, Factory#1958).

## Constraints

- No schema bump. A missing basis key keeps its old meaning.
- Live registry proof is separate: a dry run proves the code, not the deployed pod.

## Open questions

None. (Decision, owner delegation 2026-11-18: take the issue's recommendation, fall back to `hard` rather than `unknown`; it is the safest tier to over-apply.)
