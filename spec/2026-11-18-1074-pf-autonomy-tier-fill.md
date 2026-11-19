---
status: approved
issue: 1084
intent: intent/2026-11-18-1074-pf-autonomy-tier-fill.md
---

# Spec: conservative autonomy tier fallback in the plan signature

## Design

Root cause: `resolve_tier(plan_tier, risk_class)` in `apps/backend/plan/service_helpers.py:286` ends in `return "unknown", "absent"`, and `risk_class` is read from `session.epic.deployment`, which is `None` when `feasibility/deployment.py:242` finds no deployment surface (greenfield plan, e.g. session 232) or reconnaissance was unavailable.

Change the final line to `return "hard", "default-conservative"`. Routed tier (`routed`) and risk-derived tier (`risk_class`) keep precedence. `render.py` already writes `autonomy_tier_basis` from the returned pair; `risk_class` stays `unknown`/`absent` and is not invented. `SIGNATURE_SCHEMA` stays 2; `default-conservative` is an additive basis value.

## Alternatives rejected

- Derive a risk class from plan text/complexity: no such field reaches emit today; inventing one is a larger design change.
- Keep `unknown`: leaves a signed row that cannot answer "at what autonomy tier".
- Bump the schema: the field meaning is unchanged; basis keys are additive.

## Risks

Readers treating `hard` as a routing decision: it is a record only; routing reads `plan.autonomy_tier`, not the signature. Old rows with `unknown`/`absent` stay valid.

## Verification

`tests/test_plan_docs_run_signature.py`: update the absent case to `hard`/`default-conservative`; add a fixture mirroring session 232 (no deployment block, no routed tier) and keep routed and risk-derived precedence cases. Then a dry-run emit rendered through `python -m plan.governance_cli --json` shows originator, agent_identity and autonomy_tier filled. A dry run proves the code, not the live registry.
