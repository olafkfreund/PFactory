---
status: draft
issue: 830
spec: spec/2026-10-22-830-routing-pin-preserved.md
---

# Plan: the cost router keeps a contract's pinned model and declared class

Approved decisions (option (b), user decision for the 2026-10-22 wave):

- `select_phase_models` reads the prior `execution.routing` dict.
- A non-empty string `pinned_model` is a whole-task pin: all four routed roles
  get the pin, no router selection, cost priced with `estimate_cost` on the
  full catalog, `routing["pinned_model"]` kept, rationale contains
  `pin honoured: <pin>`. Non-string/empty values are ignored.
- A valid prior `class` (economy < standard < premium < governed) not weaker
  than the scorer's is kept and drives floor + ceiling. A weaker one is not
  applied; rationale records `declared class <x> below scored <y>; kept <y>`.
- Other prior keys are still replaced. `cost_router_core.py` is not touched.
- AIFactory is read only; it consumes `execution.routing.pinned_model` (str).

## Steps

1. `apps/backend/plan/emit/cost_router.py` `select_phase_models` (~:100-163)
   and `_rationale` (~:166-191): implement the pin and class rules above; update
   the module and `apply_cost_routing` docstrings to say pin/class are kept.
   → verify by `cd apps/backend && .venv/bin/python -m pytest ../../tests/test_cost_router.py -q`
   Traps: `cost_router_core.py` must stay byte-identical (drift gate); keep
   `select_phase_models` pure.
2. `tests/test_cost_router.py`: extend #826's
   `test_unavailable_model_keeps_its_price_and_explicit_run_pin` with a
   `routing.pinned_model` case (pin honoured, every role = pin); add the issue
   reproducer (pin + declared governed at medium tier), a weaker-declared-class
   case, and a non-string pin ignored case.
   → verify by the same pytest command; the new tests fail on origin/dev.
   Traps: none.

## Tests

`pytest tests/test_cost_router.py tests/test_contract_emit.py -q` green;
`ruff check` / `ruff format --check` on the two files clean.

## Rollback

Revert the PR; contracts return to replace-wholesale routing.
