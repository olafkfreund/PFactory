---
status: draft
issue: 830
intent: intent/2026-10-22-830-routing-pin-preserved.md
---

# Spec: the cost router keeps a contract's pinned model and declared class

## Design

Chosen: **option (b)** (user decision, 2026-10-22 wave). All changes in
`apps/backend/plan/emit/cost_router.py::select_phase_models`; `apply_cost_routing`
keeps assigning the returned `routing` block, which now carries the two keys.

1. **Read the prior block.** `prior = execution.routing` if it is a dict.
2. **Pin.** If `prior["pinned_model"]` is a non-empty string, every routed role
   (`planning`, `coding`, `qa`, `test_gen`) gets `phase_models[role] = pin`
   and `cheapest_capable_model` is not called. The pin is priced with
   `estimate_cost` against the full catalog (unavailable entries keep their
   price, #826), so `cost_estimate_usd` is the pin's estimate (`None` when the
   pin is unpriced/subscription). `routing["pinned_model"] = pin` and the
   rationale contains `pin honoured: <pin>`. A pin is a whole-task pin because
   that is what the consumer does: AIFactory `_resolve_phase_model` returns
   `metadata["pinnedModel"]` for every phase, and the schema's v1
   `routing.pinned_model` says "every stage uses exactly this model".
   A non-string / empty value is ignored (AIFactory drops it too).
3. **Class.** If `prior["class"]` is one of `economy|standard|premium|governed`
   and is **not weaker** than the scorer's class, it is kept and drives the
   floor and ceiling (a declared `governed` gets frontier planning and the
   governed ceiling). A declared class weaker than the scorer's is not applied
   — the schema promises `governed` is never silently degraded — and the
   rationale records `declared class <x> below scored <y>; kept <y>`.
   Rank: economy < standard < premium < governed.
4. Other prior keys are still replaced (they describe the previous decision).

AIFactory contract (read only, `apps/backend/phase_config.py:479`,
`routing_policy.py`): consumes `execution.routing.pinned_model` as a string and
pins every phase. Unchanged and now actually fed.

## Alternatives rejected

- **(a) Merge the whole prior block.** Fixes both losses but lets stale keys
  from an earlier stage (an old `cost_estimate_usd`, `rationale`) survive into a
  block that describes this decision.
- **(c) Leave as is, document.** The consumer's pin precedence stays dead code.
- **Per-role pin input.** Neither the schema nor AIFactory has one; YAGNI.
- **Preserve a declared class even when weaker than scored.** Would let a
  caller silently degrade a `governed` task, breaking the schema promise.

## Risks

- A pin to an unavailable model (e.g. `claude-opus-4-8`) is routed to on
  purpose: the pin is the explicit escape hatch. AIFactory would run it anyway.
- Contracts that already carried a weaker-than-scored class see no change;
  contracts with a stronger declared class now route stronger (cost up, by
  design).

## Verification

- `tests/test_cost_router.py`: the issue reproducer (pin + governed at medium
  tier) keeps `pinned_model`, `class == "governed"`, every role = the pin,
  rationale has `pin honoured`; a weaker declared class is not applied; no
  prior block routes as before. #826's pin-precedence test is extended from
  "the router ignores pins" to "the routing pin is honoured".
- Full `tests/test_cost_router.py` and `tests/test_contract_emit.py` green; ruff.
