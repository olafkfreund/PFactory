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
from plan.emit.cost_router_core import estimate_cost, load_catalog  # noqa: E402

_CATALOG = load_catalog(_BACKEND / "plan" / "emit" / "model-catalog.json")


def test_catalog_entries_have_router_fields():
    assert all(
        {"provider", "class", "roles", "price", "caps"} <= entry.keys()
        for entry in _CATALOG.values()
    )


def _metered_only():
    """A metered-only sub-catalog so subscription/local picks (sorted as $0)
    don't dominate; lets us assert the cheapest *priced* capable model."""
    return {k: v for k, v in _CATALOG.items() if (v.get("price") or {}).get("mode") == "metered"}


def _claude_only():
    """A metered Claude-only sub-catalog (opus/sonnet/haiku) so the
    strong-for-plan / cheap-for-code distinction is unambiguous within a tier."""
    return {k: v for k, v in _metered_only().items() if v.get("provider") == "claude"}


def test_unavailable_cheapest_is_skipped_but_absent_flag_is_available():
    catalog = {
        "unavailable": {
            "provider": "claude",
            "class": "balanced",
            "roles": ["planning"],
            "price": {"mode": "metered", "in_per_mtok": 1, "out_per_mtok": 1},
            "available": False,
        },
        "available": {
            "provider": "claude",
            "class": "balanced",
            "roles": ["planning"],
            "price": {"mode": "metered", "in_per_mtok": 2, "out_per_mtok": 2},
        },
    }
    contract = {"execution": {"autonomy_tier": "medium"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=catalog)
    assert out["phase_models"]["planning"] == "available"


# ── per-role selection ────────────────────────────────────────────────────────


def test_hard_tier_floors_planning_at_frontier():
    contract = {"execution": {"autonomy_tier": "hard"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_metered_only())
    # hard floor = frontier; planning must be the frontier model.
    assert out["phase_models"]["planning"] == "claude-opus-5-5"


def test_governed_forces_frontier_planning_even_at_medium_tier():
    contract = {
        "execution": {"autonomy_tier": "medium"},
        "deployment": {"production_classification": "production"},
        "final_acceptance": ["a"],
    }
    out = select_phase_models(contract, catalog=_metered_only())
    assert out["routing"]["class"] == "governed"
    assert out["phase_models"]["planning"] == "claude-opus-5-5"


def test_coding_is_cheaper_than_planning_within_floor():
    # medium tier: balanced floor. Within the Claude family the cheapest capable
    # coding model at/above balanced is sonnet (not opus).
    contract = {"execution": {"autonomy_tier": "medium"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_claude_only())
    assert out["phase_models"]["coding"] == "claude-sonnet-5"


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


def test_real_catalog_never_routes_to_unavailable_or_other_provider():
    # Every routed role is Claude-restricted, and unavailable catalog entries
    # remain visible for explicit pins without becoming automatic picks.
    for tier in ("low", "medium", "hard"):
        contract = {"execution": {"autonomy_tier": tier}, "final_acceptance": ["a"]}
        out = select_phase_models(contract, catalog=_CATALOG)
        pm = out["phase_models"]
        for role in ("planning", "coding", "qa", "test_gen"):
            assert pm[role].startswith("claude-"), f"{tier}/{role} resolved to {pm[role]!r}"
            assert pm[role] not in {"gemini", "claude-opus-4-8", "claude-sonnet-4-6"}


def test_low_tier_uses_claude_for_every_role():
    # All roles auto-select from Claude; low-tier coding may still use Haiku.
    contract = {"execution": {"autonomy_tier": "low"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_CATALOG)
    pm = out["phase_models"]
    # low tier -> cheap floor; cheapest claude coding-capable model is haiku.
    assert pm["coding"] == "claude-haiku-4-5-20251001"
    assert pm["qa"] == "claude-haiku-4-5-20251001"
    assert pm["test_gen"] == "claude-haiku-4-5-20251001"
    assert pm["planning"] == "claude-sonnet-5"
    assert isinstance(out["routing"]["cost_estimate_usd"], float)
    rationale = out["routing"]["rationale"]
    assert "gemini skipped: Gemini CLI terminates the session" in rationale
    assert "claude-opus-4-8 skipped: superseded by claude-opus-5-5" in rationale
    assert "claude-sonnet-4-6 skipped: superseded by claude-sonnet-5; pinnable only" in rationale


def test_medium_tier_coding_resolves_to_sonnet_by_default():
    # The common case (medium tier, full catalog): coding/qa/test_gen resolve to
    # the cheapest available balanced-class Claude model.
    contract = {"execution": {"autonomy_tier": "medium"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=_CATALOG)
    pm = out["phase_models"]
    assert pm["coding"] == "claude-sonnet-5"
    assert pm["qa"] == "claude-sonnet-5"
    assert pm["test_gen"] == "claude-sonnet-5"


def test_unavailable_model_keeps_its_price_and_explicit_run_pin():
    assert estimate_cost("claude-opus-4-8", 1_000_000, 1_000_000, _CATALOG) == 30.0
    contract = {
        "execution": {
            "autonomy_tier": "medium",
            "model": "claude-opus-4-8",
            "phase_models": {"qa_fixer": "claude-opus-4-8"},
        },
        "final_acceptance": ["a"],
    }
    apply_cost_routing(contract, catalog=_CATALOG)
    assert contract["execution"]["model"] == "claude-opus-4-8"
    assert contract["execution"]["phase_models"]["qa_fixer"] == "claude-opus-4-8"
    assert contract["execution"]["phase_models"]["planning"] == "claude-sonnet-5"


def test_planning_ceiling_relaxation_keeps_a_model():
    # A frontier model over the premium $10 per-role ceiling must still route.
    expensive = {
        **_CATALOG["claude-opus-5-5"],
        "price": {"mode": "metered", "in_per_mtok": 1000, "out_per_mtok": 1000},
    }
    catalog = {"claude-opus-5-5": expensive}
    contract = {"execution": {"autonomy_tier": "hard"}, "final_acceptance": ["a"]}
    out = select_phase_models(contract, catalog=catalog)
    assert out["phase_models"]["planning"] == "claude-opus-5-5"
    assert "planning: ceiling relaxed" in out["routing"]["rationale"]


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
