---
status: approved
issue: 1074
spec: spec/2026-11-18-1074-plan-signature-identity.md
coders:
  1: codex   # small tricky logic in three files: helpers, the _attribution extraction, the signature dict
  3: codex   # contained, mechanical test additions against a finished design
reviewers:
  - codex    # read-only review of each draft and of the final diff
---

# Plan: fill originator, agent identity and autonomy tier in the plan signature

Approved decisions (spec 2026-11-18-1074): keep intake and the completion event unchanged (null when unknown). In the signature builder `_run_signature` (`apps/backend/plan/emit/docs/render.py`) fill `originator`, `agent_identity` and `autonomy_tier` with fallbacks and add the additive keys `originator_basis`, `agent_identity_basis`, `autonomy_tier_basis`. `SIGNATURE_SCHEMA` stays 2. No hub schema change. One identity-format helper only.

Values:
- originator: `session.originator` -> basis `principal`; else `channel:<plan.source_channel>` -> basis `channel`.
- agent_identity: `session.agent_identity` -> basis `api-key`; else `agent:pfactory/planner` -> basis `pfactory-planner`.
- autonomy_tier: `plan.autonomy_tier` -> basis `routed`; else deployment `risk_class` low/medium/high -> low/medium/hard, basis `risk_class`; else `unknown`, basis `absent`.

## Steps

1. (codex) `apps/backend/plan/service_helpers.py` (append): add `agent_identity_for(key_id: str) -> str` returning `f"agent:pfactory/{key_id}"`, `PLANNER_AGENT = agent_identity_for("planner")`, `_RISK_TO_TIER = {"low": "low", "medium": "medium", "high": "hard"}`, `resolve_attribution(session) -> dict` (the four keys above) and `resolve_tier(plan_tier, risk_class) -> tuple[str, str]`. `apps/web-server/server/routes/plan_pipeline.py:218-222` (`_attribution`): build the identity with `agent_identity_for(key_id)`, keep its validation unchanged. `apps/backend/plan/emit/docs/render.py` `_run_signature` (~lines 154-167): use the helpers, add the three basis keys. Update the `SIGNATURE_SCHEMA` comment to say basis keys are additive. Check: `cd apps/backend && python -m pytest ../../tests/test_plan_docs_run_signature.py ../../tests/test_plan_intake_attribution.py ../../tests/test_plan_completion.py -q` (the "honest when absent" test is expected to need the step 3 update).
   Traps: in `service_helpers.py` import `PlanSession` only under `TYPE_CHECKING` (`plan.service` imports this module; a runtime import would be circular); do not touch `completion.py` or `PlanService.ingest_*`; `plan_pipeline._attribution` tests in `test_plan_intake_attribution.py` must pass unchanged (including the `" k-1 "` spacing case); no emails or free text in `channel:` values; ruff rules of the repo (`ruff check`, `ruff format`).
2. (lead) `apps/backend/plan/governance_cli.py` `MATCH_KEYS` and `format_result` (~lines 29-42, 207-230): add the three basis keys and print them as `originator_basis=`, `agent_identity_basis=`, `autonomy_tier_basis=` after `model_route=` (before `commits=`); old rows print `None`. Check: `tests/test_governance_cli.py` (existing line-end assertion at line ~202 is updated in step 3).
3. (codex) Tests. `tests/test_plan_docs_run_signature.py`: update `test_governance_fields_are_honest_when_absent` (originator `channel:portal` basis `channel`, agent `agent:pfactory/planner` basis `pfactory-planner`, tier `unknown` basis `absent`); add tests for: portal ingest with a user id (principal, api-key) and approval keeping the stamped values (`SERVICE.approve` then signature); upload (`ingest_bytes`); in-process MCP lane (`agent_api.plan_ingest`, `plan_approve`) -> `channel:agent`; CFactory-card session (`channel="cfactory"`, no attribution); the authenticated proxy path through the route handlers (`pp.ingest_text` with an `_Request` carrying `{"id": "u-1", "api_key_id": "k-1"}` as in `tests/test_plan_intake_attribution.py`) rendering bases `principal` and `api-key` -> `channel:cfactory`; from-issue lane (`channel="github_issue"`) -> `channel:github_issue`; tier from `risk_class` high -> `hard` basis `risk_class`, tier derivation parametrized over risk low, medium, high and an unsupported value; a routed `autonomy_tier="low"` plan with high risk stays `low` basis `routed`; completion event still emits null (existing test untouched). `tests/test_governance_cli.py`: update the exact projected-result dictionary near line 98 with the three basis keys as `None` (keep the old fixtures unchanged: they prove old rows still read); the printed line carries the three basis values and an old row without them prints `None`. `tests/test_plan_intake_attribution.py`: one test that `_attribution` uses `agent_identity_for` (equal strings). Check: `python -m pytest` on the four test files above plus `tests/test_governance_cli.py`.
   Traps: `agent_api` functions use the module singleton `agent_api.SERVICE`: monkeypatch it to the test service; reuse the existing `service`/`processed` fixtures and `_PLAN`; set a deployment `risk_class` the way `test_deployment_controls` does.

## Tests

`cd apps/backend && python -m pytest ../../tests/test_plan_docs_run_signature.py ../../tests/test_governance_cli.py ../../tests/test_plan_intake_attribution.py ../../tests/test_plan_completion.py ../../tests/test_schema_drift.py -q` all pass; `ruff check` and `ruff format --check` on touched files; then the repo's CI unit test command (see `.github/workflows/ci.yml`).

## Rollback

Revert the PR. The signature returns to null identities and `unknown` tier; old rows were never changed.

## Deviations

- Steps 1 to 3 landed in one commit: the pre-commit hook runs the Python tests, so step 1 alone (which changes the "honest when absent" expectation) cannot be committed green. Step 2 (lead) and step 1/3 (codex) are otherwise as planned.
