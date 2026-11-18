---
status: draft
issue: 1084
spec: spec/2026-11-18-1074-pf-autonomy-tier-fill.md
coders:
  1: codex   # contained one-line edit
reviewers:
  - codex    # read-only review of the final diff
---

# Plan: conservative autonomy tier fallback in the plan signature

Approved decisions (spec): `resolve_tier` falls back to `("hard", "default-conservative")` instead of `("unknown", "absent")`; routed and risk-derived precedence unchanged; `risk_class` stays unknown/absent; schema stays 2.

## Steps

1. `apps/backend/plan/service_helpers.py:286-291` (`resolve_tier`): change the final return to `"hard", "default-conservative"`. → verify by `apps/backend/.venv/bin/pytest tests/test_plan_docs_run_signature.py tests/test_governance_cli.py`
   Traps: none.
2. `tests/test_plan_docs_run_signature.py` (~l.136-137 expectation, ~l.452 parametrised cases): update the absent expectation and the "unsupported" risk case (~l.449, now `hard`/`default-conservative`); add the session-232-shaped case (no deployment block, no routed tier) asserting `hard`/`default-conservative` and `risk_class` `unknown`. Done by the lead. → same command.
3. Dry-run proof: render the signature for a processed session via the same `render_plan_docs` path (no push): write the rendered bundle (signature file plus `registry.json`) into a temporary directory and print `python -m plan.governance_cli --root <tmpdir> --json` output. Done by the lead.

## Tests

`apps/backend/.venv/bin/pytest tests/test_plan_docs_run_signature.py tests/test_governance_cli.py`, ruff check/format on touched files.

## Rollback

Revert the commit; new rows return to `unknown`/`absent`.
