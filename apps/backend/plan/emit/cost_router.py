"""RFC-0014 §5 cost-aware router — per-role model selection (pure).

Turns the RFC-0014 §4 scoring verdict (``routing_class`` + ``cost_ceiling``) and
the RFC-0011 difficulty tier into a per-role model pick, using the vendored hub
catalog (:mod:`plan.emit.cost_router_core` + ``model-catalog.json``). For each
role — ``planning``, ``coding``, ``qa``, ``test_gen`` — it picks the **cheapest
catalog model that meets the tier/class capability floor under the ceiling**:

  - ``planning`` gets the strongest floor: the tier floor, raised to *frontier*
    when the routing class is ``governed`` (a high-risk/production task is never
    silently planned by a weak model — RFC-0014 §2 + §4).
  - ``coding`` / ``qa`` / ``test_gen`` defer to the tier floor and are downgraded
    to the cheapest capable model within it (strong-for-plan, cheap-for-code) —
    but ONLY among ``claude`` models. These three roles are mechanical
    (agentic code/test writing), and a bare cheapest-wins search over the whole
    catalog lets a flat-rate *subscription* model win cost ties at $0 by
    alphabetical luck: that is how ``codex`` became the router's default coding
    model even though codex agentic builds produce NO code (empty branch, 0/N
    subtasks — AIFactory #779). Restricting these roles to ``claude`` keeps the
    tier-aware cheapest-within-floor behavior (haiku/sonnet/opus) while making
    the pick deterministic and known-to-actually-write-code. ``codex`` (and
    other providers) remain valid catalog entries and explicit/opt-in choices —
    they are just no longer auto-selected by the router for these roles.

It writes (additively) onto the contract's ``execution`` block:

  - ``phase_models`` — ``{planning, coding, qa, test_gen}`` model ids.
  - ``routing`` — ``{class, cost_estimate_usd, cost_ceiling_usd, policy,
    rationale, autonomy}`` (``cost_estimate_usd`` is ``None`` when every priced
    role resolves to a subscription/local model, mirroring CFactory billing-mode).

Runs in :func:`plan.emit.contract_emit.assemble_contract` **after**
``apply_tier`` so the tier floor is already set. Additive + back-compat: when no
tier/score is derivable the existing per-complexity ``execution`` model is left
untouched (the router only ADDS ``phase_models``/``routing``, never removes the
prior ``model``/``provider``). Pure + deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from plan.emit.cost_router_core import (
    capability_floor_class,
    cheapest_capable_model,
    estimate_cost,
    load_catalog,
    planning_floor_class,
)
from plan.emit.task_scorer import score_task

# The vendored hub catalog lives beside this module (re-vendored from
# Factory/apis/model-catalog.json — see cost_router_core's banner).
_CATALOG_PATH = Path(__file__).with_name("model-catalog.json")

# The four routed roles (RFC-0014 §2). planning is floor-raised for governed; the
# rest defer to the tier floor.
_ROLES = ("planning", "coding", "qa", "test_gen")

# The mechanical roles: agentic code/test writing, not judgment calls. Restricted
# to a single provider (below) so a cost tie among flat-rate subscription models
# can never hand the pick to a model that doesn't reliably produce code
# (AIFactory #779 — codex agentic builds exit with 0/N subtasks, empty branch).
_MECHANICAL_ROLES = ("coding", "qa", "test_gen")

# The provider mechanical roles are restricted to. claude's cheap/balanced/
# frontier tiers (haiku/sonnet/opus) already span the full capability floor
# ladder, so this preserves "cheapest capable within the tier floor" while
# guaranteeing the pick is a model proven to write real code.
_MECHANICAL_PROVIDER = "claude"

# Per-role token estimates for the rolled cost figure (in_tokens, out_tokens).
# Coarse, deterministic priors — planning reads more context and writes a plan;
# coding/qa/test_gen are mechanical. Used ONLY to compare metered models under the
# ceiling and to roll a pre-execution estimate; CFactory reconciles vs actuals.
_ROLE_TOKENS: dict[str, tuple[int, int]] = {
    "planning": (40_000, 20_000),
    "coding": (60_000, 40_000),
    "qa": (30_000, 15_000),
    "test_gen": (30_000, 20_000),
}

# RFC-0011 tier (low|medium|hard) maps to the catalog floor via the core; we read
# the tier off ``execution.autonomy_tier``. Absent -> defer to the difficulty band
# the scorer derived (so an un-tiered plan still routes within a sane floor).
_DIFFICULTY_TO_TIER = {"low": "low", "medium": "medium", "high": "hard"}


def _tier_of(contract: dict[str, Any], difficulty: str) -> str:
    execution = contract.get("execution")
    tier = ""
    if isinstance(execution, dict):
        tier = str(execution.get("autonomy_tier", "") or "").strip().lower()
    if tier in ("low", "medium", "hard"):
        return tier
    return _DIFFICULTY_TO_TIER.get(difficulty, "medium")


def _role_floor(role: str, tier: str, routing_class: str) -> str:
    """The capability floor for a role: planning is governed-aware, others defer
    to the tier floor."""
    if role == "planning":
        return planning_floor_class(tier, routing_class)
    return capability_floor_class(tier)


def _restrict_provider(catalog: dict[str, Any], provider: str) -> dict[str, Any]:
    """Return the subset of ``catalog`` whose entries are from ``provider``."""
    return {mid: entry for mid, entry in catalog.items() if entry.get("provider") == provider}


def select_phase_models(
    contract: dict[str, Any],
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pick per-role models for a contract and return the ``routing`` summary.

    Returns ``{"phase_models": {...}, "routing": {...}}``. Pure: reads the
    contract + catalog, derives the scorer verdict, and resolves the cheapest
    capable model per role under the ceiling. ``catalog`` may be injected for
    tests; otherwise the vendored hub catalog is loaded.
    """
    cat = catalog if catalog is not None else load_catalog(_CATALOG_PATH)
    score = score_task(contract)
    routing_class = score["routing_class"]
    ceiling = float(score["cost_ceiling"])
    difficulty = score["difficulty"]
    tier = _tier_of(contract, difficulty)

    phase_models: dict[str, str] = {}
    estimate_total: float | None = None
    downgrades: list[str] = []

    for role in _ROLES:
        floor = _role_floor(role, tier, routing_class)
        tokens = _ROLE_TOKENS[role]
        role_catalog = cat
        if role in _MECHANICAL_ROLES:
            # Mechanical roles never cost-shop across providers (see module
            # docstring / AIFactory #779): restrict to claude so the tier-aware
            # cheapest-within-floor pick (haiku/sonnet/opus) can never resolve to
            # codex (or another provider) on a cost tie. Fall back to the full
            # catalog only if it truly has no claude entries (defensive; never
            # true for the real vendored catalog) so a role is never dropped.
            claude_only = _restrict_provider(cat, _MECHANICAL_PROVIDER)
            if claude_only:
                role_catalog = claude_only
        pick = cheapest_capable_model(role, floor, ceiling, tokens, role_catalog)
        if pick is None:
            # Nothing cheap enough at the floor: relax the ceiling for THIS role
            # (never below the floor) so a governed/high task is never starved of
            # a capable model — the ceiling is a budget hint, the floor is hard.
            pick = cheapest_capable_model(role, floor, None, tokens, role_catalog)
            if pick is not None:
                downgrades.append(f"{role}: ceiling relaxed (no model under ${ceiling:g})")
        if pick is None:
            continue
        phase_models[role] = pick
        cost = estimate_cost(pick, tokens[0], tokens[1], cat)
        if cost is not None:
            estimate_total = (estimate_total or 0.0) + cost

    rationale = _rationale(tier, routing_class, phase_models, downgrades)
    routing = {
        "class": routing_class,
        "cost_estimate_usd": round(estimate_total, 4) if estimate_total is not None else None,
        "cost_ceiling_usd": round(ceiling, 4),
        "policy": "operator-default",
        "rationale": rationale,
        "autonomy": {"verdict": score["autonomy"], "reason": score["reason"]},
        "difficulty": difficulty,
        "risk": score["risk"],
    }
    return {"phase_models": phase_models, "routing": routing}


def _rationale(
    tier: str, routing_class: str, phase_models: dict[str, str], downgrades: list[str]
) -> str:
    floor = capability_floor_class(tier)
    parts = [
        f"tier={tier} floor={floor}; class={routing_class}",
        f"planning={phase_models.get('planning', 'n/a')}",
        f"coding={phase_models.get('coding', 'n/a')}",
        f"qa={phase_models.get('qa', 'n/a')}",
        f"test_gen={phase_models.get('test_gen', 'n/a')}",
    ]
    if routing_class == "governed":
        parts.append("governed forces frontier planning floor")
    if downgrades:
        parts.extend(downgrades)
    return "; ".join(parts)


def apply_cost_routing(
    contract: dict[str, Any],
    *,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply RFC-0014 cost routing (in place) to ``contract``; return it.

    Writes ``execution.phase_models`` (the per-role picks) and
    ``execution.routing`` (class / cost estimate / ceiling / policy / rationale /
    autonomy). Additive: it never removes the existing ``execution.model`` /
    ``provider`` (the tier/complexity default stays as the run-level fallback),
    so a consumer that ignores ``phase_models`` keeps today's behaviour.

    Must run AFTER ``apply_tier`` so the floor is set. Never raises: a catalog
    that yields no capable model for a role simply omits that role from
    ``phase_models`` (the run-level ``model`` covers it).
    """
    result = select_phase_models(contract, catalog=catalog)
    execution = contract.setdefault("execution", {})
    # Merge per-role picks over any tier-set phase_models (coding/qa/qa_fixer),
    # keeping keys the router does not own (e.g. qa_fixer) intact.
    existing = execution.get("phase_models")
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(result["phase_models"])
    execution["phase_models"] = merged
    execution["routing"] = result["routing"]
    return contract
