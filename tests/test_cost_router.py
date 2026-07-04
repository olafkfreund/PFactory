"""Tests for the RFC-0014 §5 cost-aware router (#209).

The router turns the scorer's routing_class + ceiling + the RFC-0011 tier floor
into per-role model picks from the vendored hub catalog, and writes
execution.phase_models + execution.routing. Capability floor is a hard filter;
cost is minimized only within it. governed forces frontier planning.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.emit.cost_router import apply_cost_routing, select_phase_models  # noqa: E402
from plan.emit.cost_router_core import load_catalog  # noqa: E402

_CATALOG = load_catalog(_BACKEND / "plan" / "emit" / "model-catalog.json")


def _metered_only():
    """A metered-only sub-catalog so subscription/local picks (sorted as $0)
    don't dominate; lets us assert the cheapest *priced* capable model."""
    return {k: v for k, v in _CATALOG.items() if (v.get("price") or {}).get("mode") == "metered"}


def _claude_only():
    """A metered Claude-only sub-catalog (opus/sonnet/haiku) so the
    strong-for-plan / cheap-for-code distinction is unambiguous within a tier."""
    return {k: v for k, v in _metered_only().items() if v.get("provider") == "claude"}


# ── per-role selection ────────────────────────────────────────────────────────


def test_hard_tier_floors_planning_at_frontier():
    contract = {"execution": {"autonomy_tier": "hard"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_metered_only())
    # hard floor = frontier; planning must be the frontier model.
    assert out["phase_models"]["planning"] == "claude-opus-4-8"


def test_governed_forces_frontier_planning_even_at_medium_tier():
    contract = {
        "execution": {"autonomy_tier": "medium"},
        "deployment": {"production_classification": "production"},
        "final_acceptance": ["a"],
    }
    out = select_phase_models(contract, catalog=_metered_only())
    assert out["routing"]["class"] == "governed"
    assert out["phase_models"]["planning"] == "claude-opus-4-8"


def test_coding_is_cheaper_than_planning_within_floor():
    # medium tier: balanced floor. Within the Claude family the cheapest capable
    # coding model at/above balanced is sonnet (not opus).
    contract = {"execution": {"autonomy_tier": "medium"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_claude_only())
    assert out["phase_models"]["coding"] == "claude-sonnet-4-6"


def test_low_tier_allows_cheap_coding():
    contract = {"execution": {"autonomy_tier": "low"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_claude_only())
    # low floor = cheap; the cheapest coding-capable Claude model is haiku.
    assert out["phase_models"]["coding"] == "claude-haiku-4-5-20251001"


# ── routing summary ───────────────────────────────────────────────────────────


def test_routing_block_shape():
    contract = {"execution": {"autonomy_tier": "medium"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_metered_only())
    routing = out["routing"]
    assert routing["class"] == "standard"
    assert routing["cost_ceiling_usd"] == 3.0
    assert isinstance(routing["cost_estimate_usd"], float)
    assert routing["policy"] == "operator-default"
    assert routing["autonomy"]["verdict"] == "review"
    assert "tier=medium" in routing["rationale"]


def test_mechanical_roles_never_resolve_to_codex():
    # Full catalog (every provider in the game, including the flat-rate/$0
    # subscription models codex/github-models/ollama-cloud that used to win cost
    # ties by alphabetical luck): coding/qa/test_gen must resolve to a claude
    # model, never codex — codex agentic builds produce NO code (empty branch,
    # 0/N subtasks; AIFactory #779). Planning is untouched by the restriction and
    # still cost-shops across providers, landing on the cheapest metered planner.
    for tier in ("low", "medium", "hard"):
        contract = {"execution": {"autonomy_tier": tier}, "final_acceptance": ["a"]}
        out = select_phase_models(contract, catalog=_CATALOG)
        pm = out["phase_models"]
        for role in ("coding", "qa", "test_gen"):
            assert pm[role].startswith("claude-"), f"{tier}/{role} resolved to {pm[role]!r}"
            assert pm[role] != "codex"


def test_free_runtimes_preferred_for_planning_only():
    # Full catalog, low floor: planning still cost-shops the whole catalog and
    # has no free option (codex/github-models don't serve planning), so it
    # resolves to the cheapest metered planning model. coding/qa/test_gen are
    # restricted to claude (see test_mechanical_roles_never_resolve_to_codex)
    # rather than picking the flat-rate codex that used to win here.
    contract = {"execution": {"autonomy_tier": "low"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_CATALOG)
    pm = out["phase_models"]
    # low tier -> cheap floor; cheapest claude coding-capable model is haiku.
    assert pm["coding"] == "claude-haiku-4-5-20251001"
    assert pm["qa"] == "claude-haiku-4-5-20251001"
    assert pm["test_gen"] == "claude-haiku-4-5-20251001"
    # planning is priced (gemini), so the rolled estimate includes planning's cost.
    assert pm["planning"] == "gemini"
    assert isinstance(out["routing"]["cost_estimate_usd"], float)


def test_medium_tier_coding_resolves_to_sonnet_by_default():
    # The common case (medium tier, full catalog): coding/qa/test_gen resolve to
    # claude-sonnet-4-6, matching AIFactory's DEFAULT_PHASE_MODELS["coding"].
    contract = {"execution": {"autonomy_tier": "medium"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_CATALOG)
    pm = out["phase_models"]
    assert pm["coding"] == "claude-sonnet-4-6"
    assert pm["qa"] == "claude-sonnet-4-6"
    assert pm["test_gen"] == "claude-sonnet-4-6"


def test_estimate_none_when_every_role_is_free():
    # A catalog with only flat-rate models that all serve every role -> every
    # pick is subscription/local => cost_estimate_usd is None.
    free_cat = {
        "codex": {
            "provider": "codex",
            "class": "balanced",
            "roles": ["planning", "coding", "qa", "test_gen"],
            "price": {"mode": "subscription"},
        }
    }
    contract = {"execution": {"autonomy_tier": "medium"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=free_cat)
    assert out["routing"]["cost_estimate_usd"] is None
    assert set(out["phase_models"].values()) == {"codex"}


# ── apply_cost_routing wiring ─────────────────────────────────────────────────


def test_apply_writes_execution_blocks_and_preserves_run_model():
    contract = {
        "execution": {
            "autonomy_tier": "medium",
            "model": "sonnet",
            "provider": "claude",
            "phase_models": {"qa_fixer": "sonnet"},  # router doesn't own qa_fixer
        },
        "final_acceptance": ["a"],
    }
    apply_cost_routing(contract, catalog=_metered_only())
    execution = contract["execution"]
    # Additive: the run-level model/provider stay as the fallback.
    assert execution["model"] == "sonnet"
    assert execution["provider"] == "claude"
    # Router merged its per-role picks over any tier-set phase_models, keeping
    # keys it does not own (qa_fixer).
    assert execution["phase_models"]["qa_fixer"] == "sonnet"
    assert "planning" in execution["phase_models"]
    assert execution["routing"]["class"] == "standard"


def test_apply_never_raises_on_empty_contract():
    contract: dict = {}
    apply_cost_routing(contract, catalog=_metered_only())
    assert "routing" in contract["execution"]
