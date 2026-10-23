---
status: approved
issue: 895
author: olafkfreund
---

# Intent: the cost router keeps routing.requested and routing.overrides

## Problem

`apply_cost_routing` (`apps/backend/plan/emit/cost_router.py`) rewrites
`execution.routing`. Since #893 (#830) it keeps `pinned_model` and a valid
stronger `class`, but every other prior key is replaced. AIFactory's
`trusted_plan.py` reads `execution.routing.requested` and
`execution.routing.overrides` (stage -> tier maps) to drive its per-stage
models. A producer that sets either before the router runs loses it silently.
Nothing in PFactory sets them today, so the bug is latent.

## Proposed outcome

A contract whose `execution.routing` carries a dict `requested` and/or
`overrides` before `apply_cost_routing` still carries the same dicts after it.
Non-dict values are not carried.

## Affected users and systems

- PFactory plan emit (`cost_router.py`), its tests.
- AIFactory `trusted_plan.py` (consumer, read only, unchanged).

## Constraints

- `cost_router_core.py` stays byte-identical (hub drift gate).
- `select_phase_models` stays pure; no change to the router's model choices.
- No schema change in this PR.

## Open questions

None. Owner chose decision (a): carry both keys over.
