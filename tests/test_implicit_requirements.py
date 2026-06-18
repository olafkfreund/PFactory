"""Tests for implicit service requirements (RFC-0008 §3.1, #166)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.implicit_requirements import (  # noqa: E402
    inject_into_epic,
    is_deployable_service,
    missing_requirements,
)
from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.plan_types import select_for  # noqa: E402
from plan.review.lenses.completeness import CompletenessLens  # noqa: E402
from plan.review.readiness.checks import run_readiness  # noqa: E402


def _service_plan() -> NormalizedPlan:
    # target_kind=software + "service" text → select_for picks software-service.
    return NormalizedPlan(
        plan_id="001-svc",
        title="Build a task board service",
        description="A REST API service for a kanban task board.",
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text="A task can be created and listed")],
    ).with_hash()


def _non_service_plan() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="002-sow",
        title="Quarterly statement of work",
        description="Write the Q3 statement of work document.",
        source_format="markdown",
        target_kind="non-software",
    ).with_hash()


def _epic(children: list[ChildIssue]) -> EpicPlan:
    return EpicPlan(
        plan_id="001-svc", epic_title="Task board", children=children, summary="x"
    )


def _bare_service_epic() -> EpicPlan:
    # A feature child whose only AC is a stated one — no implicit runtime ACs.
    return _epic([
        ChildIssue(
            key="C1",
            title="Task CRUD API",
            kind="feature",
            acceptance_criteria=["A task can be created and listed"],
        ),
        ChildIssue(key="C2", title="Tests", kind="testing"),
    ])


# ── is_deployable_service ────────────────────────────────────────────────


def test_is_deployable_service_true_for_software_service() -> None:
    plan = _service_plan()
    assert is_deployable_service(plan, select_for(plan)) is True


def test_is_deployable_service_false_for_non_service() -> None:
    plan = _non_service_plan()
    assert is_deployable_service(plan, select_for(plan)) is False


# ── inject_into_epic ─────────────────────────────────────────────────────


def test_inject_adds_all_missing_runtime_acs_to_feature_child() -> None:
    plan, epic = _service_plan(), _bare_service_epic()
    injected = inject_into_epic(plan, epic, select_for(plan))
    # all four implicit requirements were absent → all four injected
    assert len(injected) == 4
    feature = epic.children[0]
    acs = " ".join(feature.acceptance_criteria).lower()
    assert "start" in acs and "health" in acs
    assert "depend" in acs and "deploy" in acs
    # the testing child is untouched
    assert epic.children[1].acceptance_criteria == []


def test_inject_is_idempotent() -> None:
    plan, epic = _service_plan(), _bare_service_epic()
    first = inject_into_epic(plan, epic, select_for(plan))
    second = inject_into_epic(plan, epic, select_for(plan))
    assert first and second == []  # nothing left to add the second time
    assert not missing_requirements(epic)


def test_inject_skips_a_requirement_the_user_already_wrote() -> None:
    plan = _service_plan()
    epic = _epic([
        ChildIssue(
            key="C1",
            title="API",
            kind="feature",
            acceptance_criteria=["Exposes a /health endpoint returning 200"],
        )
    ])
    injected = inject_into_epic(plan, epic, select_for(plan))
    # health already covered → only the other three injected
    assert len(injected) == 3
    assert not any("health-check endpoint" in i for i in injected)


def test_inject_noop_for_non_service_plan() -> None:
    plan = _non_service_plan()
    epic = _epic([ChildIssue(key="C1", title="Draft the SOW", kind="task")])
    assert inject_into_epic(plan, epic, select_for(plan)) == []
    assert epic.children[0].acceptance_criteria == []


# ── CompletenessLens ─────────────────────────────────────────────────────


def test_completeness_lens_flags_missing_then_clean_after_inject() -> None:
    lens = CompletenessLens()
    plan, epic = _service_plan(), _bare_service_epic()

    before = lens.evaluate(plan, epic)
    assert before.score < 1.0
    assert any("not covered" in f.title for f in before.findings)

    inject_into_epic(plan, epic, select_for(plan))
    after = lens.evaluate(plan, epic)
    assert after.score == 1.0


def test_completeness_lens_full_score_for_non_service() -> None:
    lens = CompletenessLens()
    plan = _non_service_plan()
    score = lens.evaluate(plan, _epic([ChildIssue(key="C1", title="x", kind="task")]))
    assert score.score == 1.0
    assert score.findings == []


# ── readiness check ──────────────────────────────────────────────────────


def test_readiness_service_requirements_fail_then_pass_after_inject() -> None:
    plan, epic = _service_plan(), _bare_service_epic()
    rep = run_readiness(plan, epic)
    res = rep.result("service-requirements-covered")
    assert res.status == "fail" and res.hard
    assert res.evidence.get("missing_requirements")

    inject_into_epic(plan, epic, select_for(plan))
    rep2 = run_readiness(plan, epic)
    assert rep2.result("service-requirements-covered").status == "pass"


def test_readiness_service_requirements_not_applicable_for_non_service() -> None:
    plan = _non_service_plan()
    epic = _epic([ChildIssue(key="C1", title="Draft", kind="task")])
    rep = run_readiness(plan, epic)
    assert rep.result("service-requirements-covered").status == "not_applicable"
