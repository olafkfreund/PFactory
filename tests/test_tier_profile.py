"""Tests for the RFC-0011 tier profile override (issue #181)."""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_builder import build_task_contract
from plan.emit.contract_emit import assemble_contract
from plan.emit.execution_profile import attach_execution
from plan.emit.task_contract import validate_contract
from plan.emit.tfactory_block import attach_tfactory
from plan.emit.tier_profile import apply_tier, normalize_tier
from plan.models import Criterion, NormalizedPlan


def _plan(**kw) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget", title="Widget", source_format="markdown",
        criteria=[Criterion(id="AC#1", text="works")], **kw,
    ).with_hash()


def _epic(children: list[ChildIssue] | None = None) -> EpicPlan:
    children = children or [ChildIssue(key="C1", title="a", complexity="standard")]
    return EpicPlan(plan_id="001-widget", epic_title="Widget", children=children)


def _built() -> dict:
    plan, epic = _plan(), _epic()
    contract = build_task_contract(plan, epic)
    attach_execution(contract, plan, epic)
    attach_tfactory(contract, plan, epic)
    return contract


# ── normalize_tier ───────────────────────────────────────────────────────


def test_normalize_tier_accepts_canonical() -> None:
    assert normalize_tier("low") == "low"
    assert normalize_tier("HARD") == "hard"
    assert normalize_tier(" Medium ") == "medium"


def test_normalize_tier_strips_scope() -> None:
    assert normalize_tier("factory:hard") == "hard"


def test_normalize_tier_unknown_is_none() -> None:
    assert normalize_tier(None) is None
    assert normalize_tier("") is None
    assert normalize_tier("extreme") is None


# ── no-op when absent ──────────────────────────────────────────────────────


def test_apply_tier_noop_without_tier() -> None:
    contract = _built()
    before = {
        "model": contract["execution"]["model"],
        "skip": contract["execution"]["skip_planning"],
        "lanes": list(contract["tfactory"]["lanes"]),
    }
    apply_tier(contract, None)
    assert contract["execution"]["model"] == before["model"]
    assert contract["execution"]["skip_planning"] == before["skip"]
    assert contract["tfactory"]["lanes"] == before["lanes"]
    assert "autonomy_tier" not in contract["execution"]


# ── exact policy rows ──────────────────────────────────────────────────────


def test_low_tier_row() -> None:
    contract = _built()
    apply_tier(contract, "low")
    ex = contract["execution"]
    assert ex["model"] == "haiku"
    assert ex["provider"] == "claude"
    assert ex["skip_planning"] is True
    assert ex["review_tier"] == "auto"
    assert ex["autonomy_tier"] == "low"
    assert ex["agents"]["planner"] is False
    assert contract["tfactory"]["lanes"] == ["unit"]


def test_medium_tier_row() -> None:
    contract = _built()
    apply_tier(contract, "medium")
    ex = contract["execution"]
    assert ex["model"] == "sonnet"
    assert ex["skip_planning"] is True
    assert ex["review_tier"] == "async"
    assert ex["autonomy_tier"] == "medium"
    lanes = contract["tfactory"]["lanes"]
    assert "api" in lanes and "integration" in lanes
    assert "mutation" not in lanes


def test_hard_tier_row() -> None:
    contract = _built()
    apply_tier(contract, "hard")
    ex = contract["execution"]
    assert ex["model"] == "opus"
    assert ex["skip_planning"] is False
    assert ex["review_tier"] == "blocking"
    assert ex["autonomy_tier"] == "hard"
    assert ex["agents"]["planner"] is True
    lanes = contract["tfactory"]["lanes"]
    assert "mutation" in lanes
    assert "integration" in lanes
    assert contract["tfactory"]["coverage_target"] == 0.9


# ── safety ratchet: blocking is never lowered by a tier ─────────────────────


def test_tier_does_not_lower_blocking_review() -> None:
    contract = _built()
    # A blocking gate finding set review_tier=blocking upstream.
    contract["execution"]["review_tier"] = "blocking"
    apply_tier(contract, "low")
    # low would prefer 'auto', but blocking must stand.
    assert contract["execution"]["review_tier"] == "blocking"


def test_tier_raises_review_to_blocking_for_hard() -> None:
    contract = _built()
    contract["execution"]["review_tier"] = "auto"
    apply_tier(contract, "hard")
    assert contract["execution"]["review_tier"] == "blocking"


# ── stays schema-valid + wired through assemble_contract ───────────────────


def test_apply_tier_keeps_contract_valid() -> None:
    contract = _built()
    apply_tier(contract, "hard")
    assert validate_contract(contract) == []


def test_assemble_contract_honours_plan_tier() -> None:
    plan, epic = _plan(autonomy_tier="hard"), _epic()
    contract = assemble_contract(plan, epic)
    assert contract["execution"]["autonomy_tier"] == "hard"
    assert contract["execution"]["model"] == "opus"
    assert contract["execution"]["skip_planning"] is False
    assert "mutation" in contract["tfactory"]["lanes"]
    assert validate_contract(contract) == []
