---
status: approved
issue: 830
author: olafkfreund
---

# Intent: the cost router keeps a contract's pinned model and declared class

## Problem

`apply_cost_routing` (`apps/backend/plan/emit/cost_router.py`) builds a fresh
`execution.routing` dict and assigns it over the existing one. Anything an
earlier stage put there is lost, including:

- `pinned_model` — the RFC-0014 escape hatch. AIFactory's
  `phase_config._resolve_phase_model` checks `metadata["pinnedModel"]` first,
  and that value is carried from `execution.routing.pinned_model`. The consumer
  honours pins; the producer erases them before they get there.
- `class` — a declared `governed` becomes whatever the scorer computed
  (`standard` in the reproducer).

The router also picks a model for every role even when a pin is set, so the
signed contract's `phase_models` and rationale disagree with what will run.

## Proposed outcome

- A contract whose `execution.routing.pinned_model` is set keeps it; every
  routed role uses the pinned model, and the rationale says `pin honoured`.
- A declared `execution.routing.class` survives routing.
- Contracts without either key route exactly as today.

## Affected users and systems

PFactory plan emit (`cost_router.py`, `tests/test_cost_router.py`). AIFactory
reads the result; it is not changed.

## Constraints

- `cost_router_core.py` stays byte-identical to the hub canonical (drift gate).
- `governed` is never silently degraded (schema promise on `routing.class`).
- AIFactory's `pinnedModel` contract (a string on `execution.routing.pinned_model`)
  must still be satisfied.

## Open questions

None. User decision for this wave: option (b) — preserve `pinned_model` and
`class`; a pinned role skips router selection.
