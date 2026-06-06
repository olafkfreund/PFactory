"""Tests for the contract tfactory verify profile (epic #65, child 7)."""

from __future__ import annotations

from plan.decompose.models import ChildIssue, EpicPlan
from plan.emit.contract_builder import build_task_contract
from plan.emit.task_contract import validate_contract
from plan.emit.tfactory_block import attach_tfactory, build_tfactory
from plan.models import Criterion, NormalizedPlan


def _plan(**kw) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget", title="Widget", source_format="markdown",
        criteria=[Criterion(id="AC#1", text="works"), Criterion(id="AC#2", text="scales")], **kw,
    ).with_hash()


def _epic(children: list[ChildIssue] | None = None) -> EpicPlan:
    return EpicPlan(
        plan_id="001-widget", epic_title="Widget",
        children=children or [ChildIssue(key="C1", title="a", kind="feature")],
    )


def test_python_api_stack() -> None:
    tf = build_tfactory(_plan(description="A FastAPI service with REST endpoints, tested with pytest"), _epic())
    assert "unit" in tf["lanes"] and "api" in tf["lanes"]
    assert tf["frameworks"]["unit"] == "pytest"
    assert tf["frameworks"]["api"] == "pytest"
    assert tf["endpoints"]["api_base_url"].startswith("http")


def test_node_browser_stack() -> None:
    tf = build_tfactory(_plan(description="A React frontend built with npm, e2e via playwright"), _epic())
    assert "browser" in tf["lanes"]
    assert tf["frameworks"]["unit"] == "jest"
    assert tf["frameworks"]["browser"] == "playwright"


def test_defaults_unit_only() -> None:
    tf = build_tfactory(_plan(description="a small utility"), _epic())
    assert tf["lanes"] == ["unit"]
    assert tf["coverage_target"] == 0.8
    assert tf["security_scope"] == []  # app security out of scope by default


def test_ac_to_code_map_keyed_by_criteria() -> None:
    tf = build_tfactory(_plan(), _epic())
    assert set(tf["ac_to_code_map"].keys()) == {"AC#1", "AC#2"}
    assert tf["ac_to_code_map"]["AC#1"] == []


def test_attach_keeps_contract_valid() -> None:
    plan = _plan(description="fastapi service tested with pytest")
    epic = _epic()
    contract = attach_tfactory(build_task_contract(plan, epic), plan, epic)
    assert validate_contract(contract) == []
    assert "unit" in contract["tfactory"]["lanes"]
