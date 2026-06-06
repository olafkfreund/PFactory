"""Tests for the RFC-0002 Task Contract plan builder (epic #65, child 2)."""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_builder import build_phases, build_task_contract
from plan.emit.task_contract import validate_contract
from plan.models import Criterion, NormalizedPlan


def _plan(**kw) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget",
        title="Build the widget service",
        source_format="markdown",
        criteria=[Criterion(id="AC#1", text="exposes a widget API")],
        **kw,
    ).with_hash()


def _epic(children: list[ChildIssue]) -> EpicPlan:
    return EpicPlan(plan_id="001-widget", epic_title="Widget service", children=children)


def test_builds_schema_valid_contract() -> None:
    epic = _epic(
        [
            ChildIssue(key="C1", title="Scaffold module", kind="infra"),
            ChildIssue(key="C2", title="Implement API", kind="feature", depends_on=["C1"]),
        ]
    )
    contract = build_task_contract(_plan(), epic)
    assert validate_contract(contract) == []
    assert contract["contract_version"] == "2"
    assert contract["feature"] == "Widget service"
    assert contract["final_acceptance"] == ["exposes a widget API"]


def test_phases_are_dependency_layers() -> None:
    epic = _epic(
        [
            ChildIssue(key="C1", title="a"),
            ChildIssue(key="C2", title="b", depends_on=["C1"]),
            ChildIssue(key="C3", title="c", depends_on=["C1"]),
            ChildIssue(key="C4", title="d", depends_on=["C2", "C3"]),
        ]
    )
    phases = build_phases(epic)
    # 3 layers: {C1} -> {C2,C3} -> {C4}
    assert [p["phase"] for p in phases] == [1, 2, 3]
    assert [st["id"] for st in phases[0]["subtasks"]] == ["C1"]
    assert sorted(st["id"] for st in phases[1]["subtasks"]) == ["C2", "C3"]
    assert [st["id"] for st in phases[2]["subtasks"]] == ["C4"]
    # phase dependency edges point at the prior layer's phase numbers
    assert phases[1]["depends_on"] == [1]
    assert phases[2]["depends_on"] == [2]


def test_subtask_carries_deps_and_criteria() -> None:
    epic = _epic(
        [
            ChildIssue(key="C1", title="a"),
            ChildIssue(
                key="C2", title="b", depends_on=["C1"],
                acceptance_criteria=["does the thing"], complexity="complex",
            ),
        ]
    )
    phases = build_phases(epic)
    c2 = phases[1]["subtasks"][0]
    assert c2["depends_on"] == ["C1"]
    assert c2["acceptance_criteria"] == ["does the thing"]
    assert c2["complexity"] == "complex"
    assert c2["status"] == "pending"


def test_dangling_dep_is_dropped_from_subtask() -> None:
    epic = _epic([ChildIssue(key="C1", title="a", depends_on=["C9"])])  # C9 doesn't exist
    phases = build_phases(epic)
    assert phases[0]["subtasks"][0]["depends_on"] == []
    # dangling dep collapses to a single level-0 phase
    assert [p["phase"] for p in phases] == [1]


def test_service_inferred_from_label() -> None:
    epic = _epic([ChildIssue(key="C1", title="a", labels=["area:backend", "kind:feature"])])
    contract = build_task_contract(_plan(), epic)
    assert contract["services_involved"] == ["backend"]
    assert contract["phases"][0]["subtasks"][0]["service"] == "backend"


def test_workflow_type_from_plan_type() -> None:
    epic = _epic([ChildIssue(key="C1", title="a")])
    assert build_task_contract(_plan(plan_type="infra-change"), epic)["workflow_type"] == "migration"
    assert build_task_contract(_plan(plan_type="software-service"), epic)["workflow_type"] == "feature"
    assert build_task_contract(_plan(), epic, workflow_type="refactor")["workflow_type"] == "refactor"


def test_phase_type_voting() -> None:
    # a phase of testing children votes "integration"
    epic = _epic(
        [
            ChildIssue(key="T1", title="unit tests", kind="testing"),
            ChildIssue(key="T2", title="e2e tests", kind="testing"),
        ]
    )
    phases = build_phases(epic)
    assert phases[0]["type"] == "integration"


def test_empty_epic_yields_no_phases_but_still_validates_required() -> None:
    contract = build_task_contract(_plan(), _epic([]))
    # phases empty → schema requires >=1 phase, so this is reported invalid,
    # which is the correct signal (an undecomposed plan must not be emitted).
    assert any("phases" in e for e in validate_contract(contract))
