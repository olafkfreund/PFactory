"""Tests for the readiness waiver mechanism + approve precondition (P0.5)."""

from __future__ import annotations

import pytest
from plan.decompose.models import ChildIssue, EpicPlan
from plan.models import Criterion, NormalizedPlan
from plan.review.approval import ApprovalError, approve
from plan.review.models import PlanReview
from plan.review.readiness.checks import run_readiness
from plan.review.readiness.waiver import WaiverError, waive


def _plan(description: str = "do it") -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-widget",
        title="Build the widget",
        description=description,
        source_format="markdown",
        criteria=[Criterion(id="AC#1", text="user can log in")],
    ).with_hash()


def _review_with_uncovered_ac(plan: NormalizedPlan) -> PlanReview:
    """A passing-gates review whose readiness has one waivable hard failure."""
    # epic with NO child covering AC#1 → ac-child-coverage hard-fails.
    epic = EpicPlan(plan_id=plan.plan_id, epic_title="Widget", children=[ChildIssue(key="C1", title="unrelated")])
    review = PlanReview(plan_id=plan.plan_id, gates_passed=True)
    review.readiness = run_readiness(plan, epic)
    return review


def test_waive_clears_hard_failure() -> None:
    plan = _plan()
    review = _review_with_uncovered_ac(plan)
    assert not review.readiness.is_ready(plan)

    waive(review, plan, check_ids=["ac-child-coverage"], reason="tracked elsewhere", waived_by="olaf")
    assert review.readiness.is_ready(plan)


def test_waive_refused_on_passing_check() -> None:
    plan = _plan()
    review = _review_with_uncovered_ac(plan)
    with pytest.raises(WaiverError, match="not a hard failure"):
        waive(review, plan, check_ids=["criteria-present"], reason="x", waived_by="olaf")


def test_waive_refused_on_nonwaivable_check() -> None:
    plan = _plan()
    # children-present hard-fails and is non-waivable
    epic = EpicPlan(plan_id=plan.plan_id, epic_title="Widget", children=[])
    review = PlanReview(plan_id=plan.plan_id, gates_passed=True)
    review.readiness = run_readiness(plan, epic)
    with pytest.raises(WaiverError, match="not waivable"):
        waive(review, plan, check_ids=["children-present"], reason="x", waived_by="olaf")


def test_waive_requires_reason_and_approver() -> None:
    plan = _plan()
    review = _review_with_uncovered_ac(plan)
    with pytest.raises(WaiverError, match="reason"):
        waive(review, plan, check_ids=["ac-child-coverage"], reason="  ", waived_by="olaf")
    with pytest.raises(WaiverError, match="waived_by"):
        waive(review, plan, check_ids=["ac-child-coverage"], reason="ok", waived_by="")


def test_waive_unknown_check() -> None:
    plan = _plan()
    review = _review_with_uncovered_ac(plan)
    with pytest.raises(WaiverError, match="unknown"):
        waive(review, plan, check_ids=["does-not-exist"], reason="x", waived_by="olaf")


def test_waiver_invalidated_after_edit() -> None:
    plan = _plan()
    review = _review_with_uncovered_ac(plan)
    waive(review, plan, check_ids=["ac-child-coverage"], reason="ok", waived_by="olaf")
    assert review.readiness.is_ready(plan)

    edited = _plan(description="materially different scope now")
    review.readiness.revalidate(edited)
    assert not review.readiness.is_ready(edited)


# ── approve() precondition ─────────────────────────────────────────────────


def test_approve_refused_on_unwaived_hard_failure() -> None:
    plan = _plan()
    review = _review_with_uncovered_ac(plan)
    with pytest.raises(ApprovalError, match="readiness"):
        approve(review, plan, approver="olaf")


def test_approve_allowed_after_waive() -> None:
    plan = _plan()
    review = _review_with_uncovered_ac(plan)
    waive(review, plan, check_ids=["ac-child-coverage"], reason="ok", waived_by="olaf")
    approve(review, plan, approver="olaf")
    assert review.human_approval.approved
    assert review.ready_to_emit(plan)
