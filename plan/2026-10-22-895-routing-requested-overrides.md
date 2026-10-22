---
status: draft
issue: 895
spec: spec/2026-10-22-895-routing-requested-overrides.md
---

# Plan: the cost router keeps routing.requested and routing.overrides

Approved decisions (option (a), user decision for the 2026-10-22 wave):

- `select_phase_models` copies prior `execution.routing.requested` and
  `execution.routing.overrides` into the new routing when each is a dict
  (shallow `dict(...)` copy). Non-dict values are dropped.
- The router's model choice does not change. Other prior keys are still
  replaced (pin/class rules from #830 unchanged).
- `cost_router_core.py` is not touched. The contract schema is not touched
  (hub drift gate); AIFactory is read only.

## Steps

1. `apps/backend/plan/emit/cost_router.py` `select_phase_models` (~:188-189):
   after `if pin: routing["pinned_model"] = pin`, add
   `for k in ("requested", "overrides"): if isinstance(prior.get(k), dict): routing[k] = dict(prior[k])`.
   Update the module docstring (~:20-24) and the `apply_cost_routing`
   docstring (~:226-233) to say `requested`/`overrides` dicts are kept.
   → verify by `cd apps/backend && .venv/bin/python -m pytest ../../tests/test_cost_router.py -q`
   Traps: keep `select_phase_models` pure (copy, do not alias).
2. `tests/test_cost_router.py`: add a test that a contract with
   `execution.routing = {"requested": {...}, "overrides": {...}}` keeps equal
   (not identical) dicts after `apply_cost_routing`, and a parametrized test
   that a non-dict value (list, string) is dropped.
   → verify by the same pytest command; the new keep-test fails on origin/dev.
   Traps: tests live at repo-root `tests/`, not `apps/backend/tests`.

## Tests

- `cd apps/backend && .venv/bin/python -m pytest ../../tests/test_cost_router.py -q` → all pass.
- Stash-free check on origin/dev: `git show origin/dev:apps/backend/plan/emit/cost_router.py`
  in a temp copy → the new keep-test fails.
- `ruff check` / `ruff format --check` on both files.

## Rollback

Revert the PR commit; no data or schema migration.
