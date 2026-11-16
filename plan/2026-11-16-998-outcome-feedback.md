---
status: approved
issue: 998
spec: spec/2026-11-16-998-outcome-feedback.md
coders:
  1: you   # small tricky logic across four files in one worktree; a handoff costs more than it saves
  2: you
  3: you
reviewers:
  - codex  # read-only: independent read of the final diff against this plan
---

# Plan: build and test results reach the planner

Approved spec decisions, carried over so this file stands alone.

- Reuse existing completion events and the existing `POST /api/contract-events`; no contract, AIFactory or TFactory change.
- `ContractSyncState` gains `handback_count`, `handbacks` (last 10 `{service,status,reason,at}`), `unverified_criteria` (`None` = unknown, from `verification.traceability[]` rows not `passed`), `cost_usd_by_service` (latest `usage.cost_usd` per service, replaced not added), `seen` (last 50 dedupe keys: event `id`, else `(service,status,updated_at)`). Failure for feedback = `classify_outcome == "failure"` or `raw["outcome"] == "failure"`. Reason = `halt_reason` else status, control characters removed, 200 chars max. Reconciler `outcome`/`needs_handback` unchanged. Missing data is unknown, never zero.
- `PlanSession.downstream: ContractSyncState | None`; `summary()["downstream"]` = handback_count, unverified_criteria, actual_cost_usd, estimated_cost_usd (epic `cost_estimate.monthly_usd` if it has lines). No ratio.
- `PlanService.record_downstream(payload)` resolves the session by `raw["correlation"]["session_id"]` if known and its `correlation_key` equals the event's (ownership check), else the single session with that `correlation_key` from `_list_sessions()` only when the list is complete (0, several or incomplete: store nothing); then `get(id)` to reload it with its store version, folds into that session's own state, `_save`s. Route calls it after `REGISTRY.apply`; `StaleSessionError` reloads and refolds (3 tries) then 503.
- `PlanService.outcome_history(session)`: other sessions, same tenant, same non-null `repo`, with `downstream`, newest 5: handbacks total, up to 3 distinct reasons, distinct unverified count, estimate and actual with units stated. `""` when none. `_decompose` passes it to `decompose(history=)` -> `build_decompose_prompt(history="")`, appended block only when non-empty.

## Steps

1. `apps/backend/plan/emit/contract_sync.py`: add the fields, `_fold_feedback(state, event)` called from `reconcile`, and `fold_event(state, payload)` (parse + reconcile) for per-session use -> `pytest tests/test_contract_sync.py`
   Traps: keep stdlib/pydantic only; `reconcile` still appends to `history`; do not change `recompute`.
2. `apps/backend/plan/service.py`: `PlanSession.downstream` + `summary()` block; `record_downstream`; `outcome_history`; `_decompose` passes `history`. `apps/backend/plan/decompose/planner.py`: `history` parameter on `build_decompose_prompt`, `decompose`, `decompose_with_llm`. `apps/web-server/server/routes/contract_events.py`: call `record_downstream`, 503 on repeated stale -> `pytest tests/test_plan_service.py tests/test_decompose_planner.py tests/test_contract_sync.py`
   Traps: listed sessions carry no `_store_version`, so always `get(id)` before fold; `_all_sessions()` hides the completeness flag; `summary()` consumers and exact-key tests; `StaleSessionError` from `_save`; tenant filter before text is built; empty history must leave the prompt byte-identical.
3. Tests for every spec verification item in `tests/test_contract_sync.py`, `tests/test_plan_service.py`, `tests/test_decompose_planner.py`, plus a route test (`tests/` file for contract events, create if none) (add: incomplete-list and unreadable-row store nothing; mismatched session id/key store nothing) -> `ruff check` + `ruff format --check` on touched files, then `pytest tests/ -m "not slow"`.
   Traps: use the repo's venv at the main checkout; no network.

## Tests

`apps/backend/.venv/bin/pytest tests/ -m "not slow"` green; new tests fail on the base branch.

## Rollback

Revert the PR. `PlanSession.model_config` is `{}`, so older code ignores the stored `downstream` field on load (and drops it on its next save).

## Deviations (codex review round 1, same commit as the code)

- `record_downstream` takes `tenant_id` (route passes `resolve_tenant(request)`); sessions of another tenant are never matched.
- After `_save`, a shared store is re-read; a write that did not land raises `StaleSessionError` (so the route retries, then 503), because `_save` swallows store errors.
- A replayed event (same dedupe key) is skipped entirely, so cost and coverage never go back to older values.
- The planner text says "unknown (not reported)" for coverage and "not reported" for spend instead of zero.
- Round 2: with multi-tenancy on, the route takes the tenant from the event body (`tenant_id`, which AIFactory sends) before the `X-Tenant-Id` header; with it off there is no tenant filter. A missing estimate reads "not available", not zero. Known limit, not fixed: with only the legacy JSON disk mirror (no shared store) a failed disk write is still swallowed by `_save`.
