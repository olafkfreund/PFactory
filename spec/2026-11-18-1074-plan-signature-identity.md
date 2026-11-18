---
status: draft
issue: 1074
intent: intent/2026-11-18-1074-plan-signature-identity.md
---

# Spec: fill originator, agent identity and autonomy tier in the plan signature

## Design

All changes are in the signature builder `build_run_signature` (`apps/backend/plan/emit/docs/render.py`, the dict at lines ~119-172) plus two small helpers in `apps/backend/plan/service_helpers.py`. Intake, `PlanSession` and the completion event are unchanged (they keep honest nulls, tests in `tests/test_plan_completion.py` and `tests/test_plan_intake_attribution.py`).

1. `service_helpers.agent_identity_for(key_id)` returns `agent:pfactory/<key_id>`; `plan_pipeline._attribution` calls it instead of formatting the string itself. This is the one place the identity format lives (the #1011 helper is reused, not duplicated). `PLANNER_AGENT = agent_identity_for("planner")`.
2. `service_helpers.resolve_attribution(session)` returns `(originator, originator_basis, agent_identity, agent_identity_basis)`:
   - `originator`: `session.originator` (basis `principal`), else `channel:<plan.source_channel>` (basis `channel`).
   - `agent_identity`: `session.agent_identity` (basis `api-key`), else `PLANNER_AGENT` (basis `pfactory-planner`).
   `source_channel` is a literal allowlist (`models.SourceChannel`, includes `cfactory` since #1065), so the label never carries free text, an email or a credential.
3. `service_helpers.resolve_tier(plan_tier, risk_class)` returns `(tier, basis)`: `plan.autonomy_tier` (basis `routed`), else risk_class mapped low->low, medium->medium, high->hard (basis `risk_class`), else `("unknown", "absent")`. The mapping is the same canonical bucketing as AIFactory `merge/merge_policy.py` `_TIER_ALIASES` / deployment overlay (tier low/medium/hard decides AUTO_MERGE / HOLD_ASYNC / HOLD_BLOCKING; high risk is never autonomous); PFactory has no import path to it, so it carries only the 3-entry risk-to-tier bucket and not the disposition table, and the disposition is derived by the consumer from the tier.
4. The signature gains `originator_basis`, `agent_identity_basis`, `autonomy_tier_basis` (additive, the `risk_class_basis` pattern). `SIGNATURE_SCHEMA` stays 2: no existing key changes name or type; a reader that ignores the basis keys sees the same shape with fewer nulls, a reader that cares can tell a principal from a lane.
5. `governance_cli.MATCH_KEYS`/`format_result` print the three basis keys next to their values (already prints originator, agent_identity, autonomy_tier). Old rows without basis keys print `None`.

Entry paths: portal approval (user id present: basis principal; no principal: `channel:portal`), MCP `plan_approve` (in-process `agent_api`, channel `agent`: `channel:agent`; through the proxy with an `acw_` key: principal and api-key), CFactory card (service principal, channel `cfactory`: `channel:cfactory`).

## Alternatives rejected

- Fill at intake in `ingest_text`/`ingest_bytes`: changes the completion event and breaks #996's "null when unknown" rule and its tests.
- A second identity helper: forbidden by the issue.
- Import `merge_policy` from AIFactory: no import path across repos; duplicating the disposition table would drift.
- Leave `unknown` when no tier was routed (Codex's suggestion): kept only as the last fallback; the issue and Factory#1958 need a tier, and the basis key tells a derived tier from a routed one.
- Bump `SIGNATURE_SCHEMA` to 3: the basis keys keep meanings recoverable, and a bump would make `governance_cli` (MIN_GOVERNANCE_SCHEMA = 2) and Factory#1958 readers reject the one row we are fixing.

## Risks

- A consumer that treats any non-null `originator` as a person gets `channel:<lane>`. Mitigation: the `channel:` prefix and the basis key; there are no known consumers beyond `governance_cli`.
- `agent_identity` `agent:pfactory/planner` is asserted for plans PFactory processes even when the text was written elsewhere; the basis `pfactory-planner` states that it names the PFactory planner, not the author of the text.
- A risk-derived tier is not a routing decision (no tier override ran); the basis `risk_class` says so.

## Verification

- `pytest tests/test_plan_docs_run_signature.py tests/test_governance_cli.py tests/test_plan_intake_attribution.py tests/test_plan_completion.py`.
- New tests cover the three entry paths (portal approval, MCP `plan_approve` via `agent_api`, CFactory-card session), the tier derivation and basis, an old schema-2 row without basis keys through `governance_cli`, and that completion events still emit null.
- `ruff check` and `ruff format --check` on touched files.
