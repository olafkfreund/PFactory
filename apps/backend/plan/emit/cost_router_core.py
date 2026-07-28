#!/usr/bin/env python3
"""RFC-0014 cost-aware, capability-aware router (reference core).

A pure, dependency-free library that turns the single hub price/capability table
(`apis/model-catalog.json`) into routing decisions. PFactory's `cost_router.py`
consumes this; AIFactory/TFactory may vendor it for budget-enforce and test-model
selection. Mirrors the pure + self-test style of `scripts/verification_gate.py`.

The contract it enforces (RFC-0014 §2 "capability floor, then cost"):

  - Every model carries a *capability class* on a fixed ladder
    `local < cheap < balanced < frontier` (subscription is costed like balanced
    for the floor — it is not a quality rung, only a billing mode).
  - The RFC-0011 difficulty tier maps to a *capability floor* class. The router
    may only pick a model **at or above** that floor; within that envelope it
    minimizes cost. It NEVER makes a hard task cheap.
  - `governed` routing forces a frontier floor for planning regardless of tier,
    so a high-risk/production task is never silently planned by a weak model.
  - Cost is estimated from `price.in_per_mtok`/`out_per_mtok` for `metered`
    models only; `subscription`/`local` models have no `$` estimate (None),
    mirroring CFactory's billing-mode display.

Run directly to execute the self-tests: `python3 scripts/cost_router_core.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

# Capability ladder, weakest -> strongest. A model may be chosen only if its
# rank is >= the required floor's rank. `subscription` is a billing mode, not a
# quality rung; for floor comparison it is treated as `balanced` (see _CLASS_RANK).
CLASS_ORDER = ["local", "cheap", "balanced", "frontier"]
_CLASS_RANK = {"local": 0, "cheap": 1, "balanced": 2, "subscription": 2, "frontier": 3}

# RFC-0011 difficulty tier -> capability floor class. Unknown tiers fall back to
# the balanced floor (never below) so an unrecognized signal cannot cheapen a task.
_TIER_FLOOR = {
    "low": "cheap",
    "medium": "balanced",
    "hard": "frontier",
}

# RFC-0014 routing class -> capability floor for the PLANNING role. `governed`
# forces frontier planning; the others defer to the tier floor (None).
_GOVERNED_PLANNING_FLOOR = "frontier"


class Price(TypedDict, total=False):
    """A model's billing descriptor. `mode` is required; the per-MTok rates are
    present only for `metered` models."""

    mode: str
    in_per_mtok: float
    out_per_mtok: float


class Caps(TypedDict, total=False):
    """Capability flags used by downstream consumers (not by the floor logic)."""

    tools: bool
    thinking: bool
    context: int


# Functional TypedDict form because the catalog's capability-class key is the
# reserved word `class`, which the class-statement form cannot express. Loose by
# design (total=False): unknown keys are ignored.
ModelEntry = TypedDict(
    "ModelEntry",
    {
        "provider": str,
        "class": str,
        "roles": list[str],
        "price": Price,
        "caps": Caps,
    },
    total=False,
)


def load_catalog(path: str | Path) -> dict[str, ModelEntry]:
    """Load the hub model catalog and return its `models` map.

    Accepts either the full catalog document (with a top-level `models` object)
    or a bare model map, so callers may pass a slimmed test fixture.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("models", data) if isinstance(data, dict) else {}
    out: dict[str, ModelEntry] = {}
    if isinstance(raw, dict):
        for mid, entry in raw.items():
            if isinstance(entry, dict):
                # JSON gives an untyped dict at this I/O boundary; the row is a
                # loose ModelEntry (total=False, unknown keys ignored downstream).
                out[str(mid)] = cast("ModelEntry", entry)
    return out


def _entry_class(entry: ModelEntry) -> str:
    return str(entry.get("class", ""))


def estimate_cost(
    model: str,
    in_tokens: int,
    out_tokens: int,
    catalog: dict[str, ModelEntry],
) -> float | None:
    """Estimate the USD cost of `model` for the given token counts.

    Returns None for `subscription`/`local` models (no `$` estimate — they are
    costed as tokens/time, mirroring CFactory billing-mode display), and for an
    unknown model. Metered models are priced from their per-MTok rates.
    """
    entry = catalog.get(model)
    if entry is None:
        return None
    price = entry.get("price") or {}
    if price.get("mode") != "metered":
        return None
    in_rate = float(price.get("in_per_mtok", 0.0))
    out_rate = float(price.get("out_per_mtok", 0.0))
    return (in_tokens / 1_000_000) * in_rate + (out_tokens / 1_000_000) * out_rate


def capability_floor_class(tier: str) -> str:
    """Map an RFC-0011 difficulty tier to its capability floor class.

    Unknown tiers fall back to `balanced` (never below) so an unrecognized
    signal can never cheapen a task below the standard bar.
    """
    return _TIER_FLOOR.get(tier, "balanced")


def _meets_floor(entry: ModelEntry, floor_class: str) -> bool:
    floor_rank = _CLASS_RANK.get(floor_class, _CLASS_RANK["balanced"])
    model_rank = _CLASS_RANK.get(_entry_class(entry), -1)
    return model_rank >= floor_rank


def _serves_role(entry: ModelEntry, role: str) -> bool:
    roles = entry.get("roles") or []
    return role in roles


def _under_ceiling(cost: float | None, ceiling_usd: float | None) -> bool:
    # No ceiling, or a model with no $ estimate (subscription/local), always
    # passes the ceiling test — its spend is governed by billing mode, not $.
    if ceiling_usd is None or cost is None:
        return True
    return cost <= ceiling_usd


def _sort_key(
    item: tuple[str, ModelEntry], in_tokens: int, out_tokens: int
) -> tuple[float, int, str]:
    mid, entry = item
    cost = estimate_cost(mid, in_tokens, out_tokens, {mid: entry})
    # Order: cheapest first; among equal/None-cost models, the LOWEST capable
    # class first (don't burn a frontier model when a balanced one qualifies);
    # then model id for determinism. None cost (subscription/local) sorts as 0.0
    # so free/flat-rate models are preferred when they meet the floor.
    return (cost if cost is not None else 0.0, _CLASS_RANK.get(_entry_class(entry), 99), mid)


def cheapest_capable_model(
    role: str,
    floor_class: str,
    ceiling_usd: float | None,
    est_tokens: tuple[int, int],
    catalog: dict[str, ModelEntry],
) -> str | None:
    """Pick the cheapest catalog model that serves `role`, sits at/above
    `floor_class`, and (if metered) stays under `ceiling_usd`.

    `est_tokens` is `(in_tokens, out_tokens)`. Returns the model id, or None if
    nothing meets the floor + role + ceiling. NEVER returns a model below the
    floor: the floor is a hard filter applied before cost is considered.
    """
    in_tokens, out_tokens = est_tokens
    candidates = [
        (mid, entry)
        for mid, entry in catalog.items()
        if _serves_role(entry, role)
        and _meets_floor(entry, floor_class)
        and _under_ceiling(estimate_cost(mid, in_tokens, out_tokens, {mid: entry}), ceiling_usd)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: _sort_key(item, in_tokens, out_tokens))
    return candidates[0][0]


def planning_floor_class(tier: str, routing_class: str) -> str:
    """Capability floor for the PLANNING role: the stronger of the tier floor and
    the routing-class floor. `governed` forces frontier planning (RFC-0014 §4)."""
    base = capability_floor_class(tier)
    if routing_class == "governed":
        forced_rank = _CLASS_RANK[_GOVERNED_PLANNING_FLOOR]
        if _CLASS_RANK.get(base, 0) < forced_rank:
            return _GOVERNED_PLANNING_FLOOR
    return base


def admit_within_budget(
    *,
    active_jobs: int,
    max_concurrency: int | None,
    tenant_spend_usd: float,
    ceiling_usd: float | None,
    est_job_cost_usd: float | None,
) -> tuple[str, str]:
    """Admission decision tying RFC-0016 concurrency control to the RFC-0014 budget.

    The single gate a control plane consults before starting another concurrent
    Job. Returns ``(decision, reason)`` where decision is:

    - ``"queue"`` — the concurrency cap is full (RFC-0016); wait for a slot.
    - ``"deny"``  — admitting would exceed the tenant/team spend ceiling (RFC-0014).
    - ``"admit"`` — under both the cap and the budget.

    Concurrency is checked first (a full fleet always queues, regardless of cost).
    A metered estimate of ``None`` (subscription/local billing — see
    ``estimate_cost``) never charges the budget. ``max_concurrency`` None/<=0 means
    unlimited; ``ceiling_usd`` None means no budget enforcement (observe-only).
    """
    if max_concurrency is not None and max_concurrency > 0 and active_jobs >= max_concurrency:
        return ("queue", f"concurrency cap reached ({active_jobs}/{max_concurrency})")
    if ceiling_usd is not None and est_job_cost_usd is not None:
        projected = tenant_spend_usd + est_job_cost_usd
        if projected > ceiling_usd:
            return ("deny", f"budget: ${projected:.2f} would exceed ${ceiling_usd:.2f} ceiling")
    return ("admit", "within concurrency cap and budget")


# --------------------------------------------------------------------------- #
# Self-tests (run: python3 scripts/cost_router_core.py)
# --------------------------------------------------------------------------- #
def _fixture() -> dict[str, ModelEntry]:
    return {
        "claude-opus-4-8": {
            "provider": "claude",
            "class": "frontier",
            "roles": ["planning", "coding", "qa", "test_gen"],
            "price": {"mode": "metered", "in_per_mtok": 15, "out_per_mtok": 75},
        },
        "claude-sonnet-4-6": {
            "provider": "claude",
            "class": "balanced",
            "roles": ["planning", "coding", "qa", "test_gen"],
            "price": {"mode": "metered", "in_per_mtok": 3, "out_per_mtok": 15},
        },
        "claude-haiku-4-5-20251001": {
            "provider": "claude",
            "class": "cheap",
            "roles": ["coding", "qa", "test_gen"],
            "price": {"mode": "metered", "in_per_mtok": 0.8, "out_per_mtok": 4},
        },
        "ollama:<model>": {
            "provider": "ollama",
            "class": "local",
            "roles": ["coding", "qa", "test_gen"],
            "price": {"mode": "local"},
        },
        "codex": {
            "provider": "codex",
            "class": "balanced",
            "roles": ["coding", "qa", "test_gen"],
            "price": {"mode": "subscription"},
        },
    }


def _check(ok: bool, detail: str) -> None:
    # Self-tests use a raising checker rather than `assert` so the module stays
    # ruff-clean (S101) for the new-file ratchet, and stays meaningful under -O.
    if not ok:
        raise AssertionError(detail)


def _test_estimate(cat: dict[str, ModelEntry]) -> None:
    # Metered: 1M in + 1M out at opus rates = 15 + 75 = 90.
    opus_full_mtok = 15.0 + 75.0
    cost = estimate_cost("claude-opus-4-8", 1_000_000, 1_000_000, cat)
    _check(cost == opus_full_mtok, f"opus 1M/1M cost {cost} != {opus_full_mtok}")
    # Subscription / local / unknown -> None (no $ estimate).
    _check(estimate_cost("codex", 1_000_000, 1_000_000, cat) is None, "subscription must be None")
    _check(estimate_cost("ollama:<model>", 1_000_000, 1_000_000, cat) is None, "local must be None")
    _check(estimate_cost("nope", 10, 10, cat) is None, "unknown model must be None")


def _test_floor(cat: dict[str, ModelEntry]) -> None:
    _check(capability_floor_class("low") == "cheap", "low floor")
    _check(capability_floor_class("medium") == "balanced", "medium floor")
    _check(capability_floor_class("hard") == "frontier", "hard floor")
    _check(capability_floor_class("???") == "balanced", "unknown tier never below balanced")

    # Floor respected: a `hard` (frontier) coding pick must be the frontier model,
    # never the cheaper balanced/cheap ones, even though they are cheaper.
    pick = cheapest_capable_model("coding", "frontier", None, (100_000, 100_000), cat)
    _check(pick == "claude-opus-4-8", f"frontier floor must pick opus, got {pick}")


def _test_cheapest_under_ceiling(cat: dict[str, ModelEntry]) -> None:
    # Constrain to a metered-only sub-catalog: with the full catalog the
    # subscription/local options sort as cost 0.0 and would win. Among metered
    # models at/above the balanced floor, the cheapest is sonnet (haiku is below).
    metered = {k: v for k, v in cat.items() if (v.get("price") or {}).get("mode") == "metered"}
    pick = cheapest_capable_model("coding", "balanced", 5.0, (100_000, 100_000), metered)
    _check(pick == "claude-sonnet-4-6", f"cheapest balanced metered must be sonnet, got {pick}")

    # A tight ceiling that sonnet's estimate exceeds forces None (no metered model
    # under it at/above the floor): sonnet on 1M/1M = 3+15 = 18 > 1.0.
    none_pick = cheapest_capable_model("coding", "balanced", 1.0, (1_000_000, 1_000_000), metered)
    _check(none_pick is None, f"nothing under a $1 ceiling at balanced floor, got {none_pick}")

    # cheap floor, looser ceiling: haiku (0.8+4=4.8) is the cheapest capable.
    haiku_pick = cheapest_capable_model("coding", "cheap", 10.0, (1_000_000, 1_000_000), metered)
    _check(haiku_pick == "claude-haiku-4-5-20251001", f"cheap floor must pick haiku, {haiku_pick}")


def _test_subscription_local_preferred(cat: dict[str, ModelEntry]) -> None:
    # Full catalog, cheap floor: ollama is `local` (below the cheap floor) so it is
    # excluded; codex (subscription, balanced) has no $ estimate (sorts as 0.0) and
    # meets the floor, so it is preferred over the metered haiku/sonnet/opus.
    pick = cheapest_capable_model("coding", "cheap", None, (1_000_000, 1_000_000), cat)
    _check(pick == "codex", f"flat-rate codex preferred at cheap floor, got {pick}")


def _test_governed_forces_frontier(cat: dict[str, ModelEntry]) -> None:
    # governed routing forces a frontier PLANNING floor even on a medium tier,
    # so planning must resolve to the frontier model.
    floor = planning_floor_class("medium", "governed")
    _check(floor == "frontier", f"governed forces frontier planning floor, got {floor}")
    pick = cheapest_capable_model("planning", floor, None, (50_000, 50_000), cat)
    _check(pick == "claude-opus-4-8", f"governed planning must pick opus, got {pick}")

    # Non-governed medium planning defers to the tier floor (balanced).
    base = planning_floor_class("medium", "standard")
    _check(base == "balanced", f"standard medium planning defers to balanced, got {base}")


def _test_admission_budget() -> None:
    # Under cap and under budget -> admit.
    d, _ = admit_within_budget(
        active_jobs=2,
        max_concurrency=5,
        tenant_spend_usd=10.0,
        ceiling_usd=100.0,
        est_job_cost_usd=3.0,
    )
    _check(d == "admit", f"under cap+budget must admit, got {d}")
    # Cap full -> queue (concurrency checked before budget).
    d, _ = admit_within_budget(
        active_jobs=5,
        max_concurrency=5,
        tenant_spend_usd=0.0,
        ceiling_usd=None,
        est_job_cost_usd=None,
    )
    _check(d == "queue", f"full cap must queue, got {d}")
    # Over budget -> deny.
    d, _ = admit_within_budget(
        active_jobs=0,
        max_concurrency=5,
        tenant_spend_usd=99.0,
        ceiling_usd=100.0,
        est_job_cost_usd=5.0,
    )
    _check(d == "deny", f"over-budget must deny, got {d}")
    # Subscription/local (None est) never charges the budget -> admit near the ceiling.
    d, _ = admit_within_budget(
        active_jobs=0,
        max_concurrency=5,
        tenant_spend_usd=99.0,
        ceiling_usd=100.0,
        est_job_cost_usd=None,
    )
    _check(d == "admit", f"flat-rate job must not be budget-denied, got {d}")
    # Unlimited concurrency + no ceiling -> always admit.
    d, _ = admit_within_budget(
        active_jobs=999,
        max_concurrency=None,
        tenant_spend_usd=1e9,
        ceiling_usd=None,
        est_job_cost_usd=1e9,
    )
    _check(d == "admit", f"unlimited+no-ceiling must admit, got {d}")


def _test() -> None:
    cat = _fixture()
    _test_estimate(cat)
    _test_floor(cat)
    _test_cheapest_under_ceiling(cat)
    _test_subscription_local_preferred(cat)
    _test_governed_forces_frontier(cat)
    _test_admission_budget()
    print("cost_router_core self-tests: 6 groups passed")  # noqa: T201  # CLI self-test report sink


if __name__ == "__main__":
    _test()
