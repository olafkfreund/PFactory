"""Tests for Task Contract verification specs + required_commands (epic #65)."""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_builder import build_task_contract
from plan.emit.task_contract import validate_contract
from plan.emit.verification import (
    attach_verification,
    build_verification,
    derive_required_commands,
)
from plan.models import Criterion, NormalizedPlan


def _plan(**kw) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget", title="Widget", source_format="markdown",
        criteria=[Criterion(id="AC#1", text="works")], **kw,
    ).with_hash()


def _epic(children: list[ChildIssue]) -> EpicPlan:
    return EpicPlan(plan_id="001-widget", epic_title="Widget", children=children)


def test_verification_by_kind() -> None:
    assert build_verification(ChildIssue(key="T", title="t", kind="testing")) == {
        "type": "command", "run": "pytest"
    }
    assert build_verification(ChildIssue(key="D", title="d", kind="docs")) == {"type": "none"}
    manual = build_verification(
        ChildIssue(key="C", title="c", kind="feature", acceptance_criteria=["a", "b"])
    )
    assert manual["type"] == "manual"
    assert manual["scenario"] == "a; b"
    assert build_verification(ChildIssue(key="C2", title="c2", kind="feature")) == {"type": "none"}


def test_required_commands_python_stack() -> None:
    plan = _plan(description="A Python service tested with pytest and mypy")
    epic = _epic([ChildIssue(key="C1", title="a")])
    cmds = derive_required_commands(plan, epic)
    assert "pytest" in cmds and "ruff" in cmds and "mypy" in cmds


def test_required_commands_node_stack() -> None:
    plan = _plan(description="A node app using jest and playwright via npm")
    cmds = derive_required_commands(plan, _epic([ChildIssue(key="C1", title="a")]))
    assert "jest" in cmds and "playwright" in cmds and "npm" in cmds


def test_testing_kind_triggers_python_commands() -> None:
    # even without text signals, a testing child implies the python lane
    cmds = derive_required_commands(_plan(), _epic([ChildIssue(key="T1", title="t", kind="testing")]))
    assert {"uv", "pytest", "ruff"} <= set(cmds)


def test_attach_keeps_contract_valid_and_sets_fields() -> None:
    epic = _epic([
        ChildIssue(key="C1", title="Build", kind="feature", acceptance_criteria=["does X"]),
        ChildIssue(key="T1", title="Test", kind="testing", depends_on=["C1"]),
    ])
    plan = _plan(description="python service with pytest")
    contract = attach_verification(build_task_contract(plan, epic), plan, epic)
    assert validate_contract(contract) == []
    # C1 → manual, T1 → command
    subtasks = {st["id"]: st for ph in contract["phases"] for st in ph["subtasks"]}
    assert subtasks["C1"]["verification"]["type"] == "manual"
    assert subtasks["T1"]["verification"] == {"type": "command", "run": "pytest"}
    assert "pytest" in contract["required_commands"]
