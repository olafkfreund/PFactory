"""Tests for the Task Contract execution profile (epic #65, child 3)."""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_builder import build_task_contract
from plan.emit.execution_profile import (
    attach_execution,
    build_execution,
    derive_complexity,
)
from plan.emit.task_contract import validate_contract
from plan.models import Criterion, NormalizedPlan


def _plan(**kw) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget", title="Widget", source_format="markdown",
        criteria=[Criterion(id="AC#1", text="works")], **kw,
    ).with_hash()


def _epic(children: list[ChildIssue]) -> EpicPlan:
    return EpicPlan(plan_id="001-widget", epic_title="Widget", children=children)


def test_complexity_takes_max_child() -> None:
    epic = _epic([
        ChildIssue(key="C1", title="a", complexity="simple"),
        ChildIssue(key="C2", title="b", complexity="complex"),
    ])
    assert derive_complexity(epic) == "complex"


def test_complexity_service_bump() -> None:
    epic = _epic([ChildIssue(key="C1", title="a", complexity="simple")])
    assert derive_complexity(epic, services=0) == "simple"
    assert derive_complexity(epic, services=3) == "standard"


def test_model_and_provider_by_complexity() -> None:
    simple = build_execution(_plan(), _epic([ChildIssue(key="C1", title="a", complexity="simple")]))
    assert simple["model"] == "haiku"
    assert simple["provider"] == "claude"
    assert simple["skip_planning"] is True
    assert simple["agents"]["planner"] is False

    complex_ex = build_execution(_plan(), _epic([ChildIssue(key="C1", title="a", complexity="complex")]))
    assert complex_ex["model"] == "opus"
    assert complex_ex["phase_thinking"]["coding"] == "high"


def test_attach_execution_keeps_contract_valid() -> None:
    epic = _epic([
        ChildIssue(key="C1", title="Scaffold", kind="infra"),
        ChildIssue(key="C2", title="Build", kind="feature", depends_on=["C1"], complexity="complex"),
    ])
    contract = attach_execution(build_task_contract(_plan(), epic), _plan(), epic)
    assert validate_contract(contract) == []
    assert contract["execution"]["complexity"] == "complex"
    assert contract["execution"]["provider"] == "claude"


def test_phase_models_cover_coding_and_qa() -> None:
    ex = build_execution(_plan(), _epic([ChildIssue(key="C1", title="a", complexity="standard")]))
    assert ex["phase_models"] == {"coding": "sonnet", "qa": "sonnet", "qa_fixer": "sonnet"}
