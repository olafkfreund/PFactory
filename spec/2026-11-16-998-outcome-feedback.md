---
status: draft
issue: 998
intent: intent/2026-11-16-998-outcome-feedback.md
---

# Spec: build and test results reach the planner

## Design

Keep the existing events, endpoint and reconciler. Add a feedback record to the state the reconciler already keeps, store it on the plan session (which already persists through `PlanService._save`), and read it back when decomposing.

**1. Feedback fields on `ContractSyncState`** (`apps/backend/plan/emit/contract_sync.py`), all additive with defaults:
- `handback_count: int = 0` and `handbacks: list[dict]`: the last 10 `{service, status, reason, at}`. A "failure" here is `classify_outcome(...) == "failure"` or an explicit `raw["outcome"] == "failure"` (TFactory can emit `status: triaged` with `outcome: failure`). `reason` is `raw["halt_reason"]` or else the status, control characters removed, cut at 200 characters. A repeat of an identical `(service, status, updated_at)` is not counted twice (webhook retries). The reconciler's `outcome` and `needs_handback` are untouched.
- `unverified_criteria: list[str] | None = None`: the `ac_id` of every `verification.traceability[]` row whose status is not `passed`, from the latest TFactory event that carried a traceability list. `None` means unknown, `[]` means all verified.
- `cost_usd_by_service: dict[str, float]`: the latest `usage.cost_usd` per service (events are cumulative snapshots, so replace, not add). `actual_cost_usd` is their sum, or `None` when no service reported a cost.

`reconcile()` calls one new helper, `_fold_feedback(state, event)`, so the fields update in the same step as the status.

**2. Store on the session.** `PlanSession` gains `downstream: ContractSyncState | None = None` (old payloads load as null) and `summary()` gains a `downstream` block: `handback_count`, `unverified_criteria`, `actual_cost_usd`, `estimated_cost_usd` (the epic's `cost_estimate.monthly_usd` when it has priced lines, else null) and `cost_ratio` (actual / estimated when both are positive, else null). `PlanService.record_downstream(payload_state)` finds the session and saves it. The session is chosen by `raw["correlation"]["session_id"]` when present and known, else the single session with that `correlation_key`; zero or several matches store nothing (issue numbers can repeat across repositories, and a wrong attachment is worse than none).

**3. Route** (`routes/contract_events.py`): before `REGISTRY.apply`, seed the registry from the session's stored `downstream` when the registry has no entry (restart continuity); after it, call `record_downstream`. The response is unchanged.

**4. Planner input.** `PlanService.outcome_history(session)` builds a short text from the other sessions in the same tenant with the same `repo` that have a `downstream` record (newest 5): total handbacks, up to 3 distinct recent reasons, count of distinct unverified criteria, and the estimate/actual figures with the unit stated ("estimate is monthly cloud cost, actual is build and verify token spend"). It returns `""` with no history. `_decompose` passes it to `decompose(..., history=...)`, which hands it to `build_decompose_prompt(..., history="")`; a non-empty value adds a "Outcomes of earlier plans for this repository" block at the end of the prompt. Empty adds nothing, so the prompt is identical to today's.

## Alternatives rejected

- A separate outcomes table and migration: more moving parts; sessions already persist and carry `repo`, tenant and the estimate.
- Persisting the registry itself: it is keyed by correlation key alone, has no tenant or repo, and cannot answer "the same repository".
- Adding fields to the contract or to AIFactory/TFactory emitters: the events already carry usage, `halt_reason`, `outcome` and traceability when available.
- Feeding history to the heuristic decomposer or the review gates: the heuristic ignores prose; scope is the decompose input the issue names.

## Risks

- Estimate (monthly cloud cost) and actual (token spend) are different quantities; the ratio is shown with that stated and never used as a gate.
- Free text from another service reaches an LLM prompt: bounded (200 chars, 3 reasons), control characters removed, labelled as data. A hostile `halt_reason` can still be read as text; it is advisory context only and the tenant boundary is enforced by the session filter.
- Sessions that never persist (no `DATABASE_URL`, persistence off) lose the record on restart, as they lose everything else.
- Sessions saved here race with other writers; `_save` already raises `StaleSessionError`, which the route catches and logs without failing the event.

## Verification

Unit tests in `tests/test_contract_sync.py` (handback reasons, explicit `outcome: failure`, retry dedupe, unverified criteria, latest-cost replace, unknown stays `None`), `tests/test_plan_service.py` (record on the right session, ambiguous key stores nothing, summary block, restart reload from disk, history text with tenant and repo filter, empty with no history), `tests/test_decompose_planner.py` (history in the prompt; absent prompt identical to today's), and a route test through the FastAPI client. `ruff` and `pytest tests/ -m "not slow"` as CI does.
