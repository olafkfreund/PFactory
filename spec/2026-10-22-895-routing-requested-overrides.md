---
status: draft
issue: 895
intent: intent/2026-10-22-895-routing-requested-overrides.md
---

# Spec: the cost router keeps routing.requested and routing.overrides

## Design

Decision (a), chosen by the owner: carry both keys over.

In `apps/backend/plan/emit/cost_router.py` `select_phase_models`, right after
`if pin: routing["pinned_model"] = pin`, copy `requested` and `overrides` from
the prior `execution.routing` when each is a dict:

```python
for k in ("requested", "overrides"):
    if isinstance(prior.get(k), dict):
        routing[k] = dict(prior[k])
```

A shallow copy, so the returned routing does not alias the input contract
(`select_phase_models` stays pure). Non-dict values are dropped, as other
unknown prior keys are. The router does not read either key for its own model
choice; they are carried for AIFactory only. The `apply_cost_routing` and
module docstrings name the two keys as kept.

Shape check: AIFactory `apps/backend/trusted_plan.py` (~:750-765) reads
`routing.get("requested")` / `routing.get("overrides")`, accepts only a dict,
and keeps string tier values per stage. A copied dict matches.

Schema note: `task-contract.schema.json` documents `requested`/`overrides` on
the top-level contract `routing` block (RFC-0014 v1 policy carrier), not on
`execution.routing`, which is an open object (`additionalProperties: true`), so
carrying them validates. The schema is not changed here: PFactory's copy must
stay identical to the hub's `Factory/apis/task-contract.schema.json`
(`tests/test_schema_drift.py`), so even a description note needs a hub change
first. Left as a follow-up.

## Alternatives rejected

- (b) Producers must set the keys after routing, documented only: leaves the
  silent drop in place for any producer that runs before the router.
- Keep every prior key: would also keep stale `rationale`/`cost_estimate_usd`
  and other router-owned fields, which #830 decided must be replaced.
- Deep copy / validate tier values: AIFactory already filters values; a
  shallow copy of the map is enough.

## Risks

- None at runtime today: no PFactory producer sets the keys yet, so emitted
  contracts are unchanged.
- `cost_router_core.py` is not touched, so the hub drift gate is unaffected.

## Verification

- New tests in `tests/test_cost_router.py`: both dicts survive
  `apply_cost_routing` (equal, not the same object); a non-dict value is
  dropped. They fail on `origin/dev`.
- `pytest tests/test_cost_router.py`, ruff on touched files, CI green.
