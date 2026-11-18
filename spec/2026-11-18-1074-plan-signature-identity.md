---
status: approved
issue: 1074
intent: intent/2026-11-18-1074-plan-signature-identity.md
---

# Spec: fill originator, agent identity and autonomy tier in the plan signature

## Design

All changes are in the signature builder `_run_signature` (`apps/backend/plan/emit/docs/render.py`, the dict at lines ~119-172) plus small new helpers in `apps/backend/plan/service_helpers.py`. Intake, `PlanSession` and the completion event are unchanged (they keep honest nulls, tests in `tests/test_plan_completion.py` and `tests/test_plan_intake_attribution.py`).

1. New `service_helpers.agent_identity_for(key_id)` returns `agent:pfactory/<key_id>`, extracted from the inline f-string in `plan_pipeline._attribution` (which keeps its own validation of `api_key_id` and calls the helper for the format). This is the one place the identity format lives (the #1011 helper is reused, not duplicated). `PLANNER_AGENT = agent_identity_for("planner")`.
2. `service_helpers.resolve_attribution(session)` returns `(originator, originator_basis, agent_identity, agent_identity_basis)`:
   - `originator`: `session.originator` (basis `principal`), else `channel:<plan.source_channel>` (basis `channel`).
   - `agent_identity`: `session.agent_identity` (basis `api-key`), else `PLANNER_AGENT` (basis `pfactory-planner`).
   `source_channel` is a literal allowlist (`models.SourceChannel`, includes `cfactory` since #1065), so the label never carries free text, an email or a credential.
3. `service_helpers.resolve_tier(plan_tier, risk_class)` returns `(tier, basis)`: `plan.autonomy_tier` (basis `routed`), else risk_class mapped low->low, medium->medium, high->hard (basis `risk_class`), else `("unknown", "absent")`. The mapping is the same canonical bucketing as AIFactory `merge/merge_policy.py` `_TIER_ALIASES` / deployment overlay (tier low/medium/hard decides AUTO_MERGE / HOLD_ASYNC / HOLD_BLOCKING; high risk is never autonomous); PFactory has no import path to it, so it carries only the 3-entry risk-to-tier bucket and not the disposition table, and the disposition is derived by the consumer from the tier.
4. The signature gains `originator_basis`, `agent_identity_basis`, `autonomy_tier_basis` (additive, the `risk_class_basis` pattern). `SIGNATURE_SCHEMA` stays 2: no existing key changes name or type; a reader that ignores the basis keys sees the same shape with fewer nulls, a reader that cares can tell a principal from a lane.
5. `governance_cli.MATCH_KEYS`/`format_result` print the three basis keys next to their values (already prints originator, agent_identity, autonomy_tier). Old rows without basis keys print `None`.

Entry paths. Attribution is stamped at INTAKE; approval (`SERVICE.approve`, `agent_api.plan_approve`, the portal approve route, the MCP proxy) only changes the review state and leaves the stamped values alone. So each path is specified by how the session came in, and approval is tested separately to keep them:
- portal ingest (text, upload) with a user id: basis principal; with the default user or a service principal: `channel:portal`;
- MCP: in-process `agent_api.plan_ingest` (channel `agent`): `channel:agent` / planner; through the proxy with an `acw_` key: user id and `agent:pfactory/<key id>` (basis principal, api-key);
- CFactory card (service principal, channel `cfactory`): `channel:cfactory` / planner;
- from-issue (`github_issue`): originator is deliberately null at intake (the issue author is unknown), so the signature shows `channel:github_issue`, basis `channel`, never a person.

## Alternatives rejected

- Fill at intake in `ingest_text`/`ingest_bytes`: changes the completion event and breaks #996's "null when unknown" rule and its tests.
- A second identity helper: forbidden by the issue.
- Import `merge_policy` from AIFactory: no import path across repos; duplicating the disposition table would drift.
- Leave `unknown` when no tier was routed (Codex's suggestion): kept only as the last fallback; the issue and Factory#1958 need a tier, and the basis key tells a derived tier from a routed one.
- Bump `SIGNATURE_SCHEMA` to 3: `governance_cli` accepts `>= 2`, but the owner's issue requires schema 2 to stay backward compatible, and remote readers (Factory#1958 tooling, CFactory) were not verified to accept 3. Accepted trade-off: readers that ignore the basis keys see a lane label as originator, and `governance_cli --tier hard` matches risk-derived rows alongside routed ones; that is the intended reading for Factory#1958 (the question is the tier the change is held at), and the basis key is printed beside it.

## Risks

- A consumer that treats any non-null `originator` as a person gets `channel:<lane>`. Mitigation: the `channel:` prefix and the basis key; there are no known consumers beyond `governance_cli`.
- `agent_identity` `agent:pfactory/planner` is asserted for plans PFactory processes even when the text was written elsewhere; the basis `pfactory-planner` states that it names the PFactory planner, not the author of the text.
- A risk-derived tier is not a routing decision (no tier override ran); the basis `risk_class` says so.

## Verification

- `pytest tests/test_plan_docs_run_signature.py tests/test_governance_cli.py tests/test_plan_intake_attribution.py tests/test_plan_completion.py`.
- New tests cover the entry paths above (portal ingest + approve, MCP `agent_api` ingest + `plan_approve`, CFactory-card ingest, upload, from-issue), that approval keeps the stamped attribution, the tier derivation and basis, an old schema-2 row without basis keys through `governance_cli`, and that completion events still emit null.
- `ruff check` and `ruff format --check` on touched files.
