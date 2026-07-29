"""Tests for the human-approval gate with content-hash invalidation (#17)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.review.approval import (  # noqa: E402
    ApprovalError,
    add_feedback,
    approve,
    is_valid,
    load_approval,
    reject,
    revalidate,
    save_approval,
)
from plan.review.models import Finding, LensScore, PlanReview  # noqa: E402


def _passing_review() -> PlanReview:
    """A review whose lenses all pass the threshold."""
    lenses = [
        LensScore(lens="architecture", score=0.9),
        LensScore(lens="security", score=0.9),
        LensScore(lens="feasibility", score=0.9),
    ]
    return PlanReview(plan_id="001-x", lenses=lenses).recompute()


def _failing_review() -> PlanReview:
    """A review whose gates fail (one lens below threshold)."""
    lenses = [
        LensScore(lens="architecture", score=0.9),
        LensScore(lens="security", score=0.4),
    ]
    return PlanReview(plan_id="001-x", lenses=lenses).recompute()


def _plan() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-x",
        title="Build widget",
        description="A widget that does things.",
        source_format="markdown",
        criteria=[Criterion(id="AC#1", text="It works")],
    ).with_hash()


def test_approve_sets_fields_and_enables_emit():
    review = _passing_review()
    plan = _plan()

    approve(review, plan, approver="olaf", at="2026-06-03T00:00:00+00:00")

    approval = review.human_approval
    assert approval.approved is True
    assert approval.approved_by == "olaf"
    assert approval.approved_at == "2026-06-03T00:00:00+00:00"
    assert approval.plan_hash == plan.content_hash
    assert approval.valid is True
    assert approval.review_count == 1
    assert review.ready_to_emit() is True
    assert is_valid(review, plan) is True


def test_approve_requires_passing_gates():
    review = _failing_review()
    plan = _plan()

    with pytest.raises(ApprovalError):
        approve(review, plan, approver="olaf")

    assert review.human_approval.approved is False


def _issue_387_review() -> PlanReview:
    """The live review from #387: aggregate 0.94, one lens at 0.70, nothing flagged.

    The finding that actually blocks approval is ``blocking=False`` — the score
    is the gate, not the flag.
    """
    lenses = [
        LensScore(lens="feasibility", score=1.0),
        LensScore(lens="architecture", score=1.0),
        LensScore(
            lens="security",
            score=0.7,
            findings=[
                Finding(
                    title="No authentication/authorization criteria",
                    detail="Confirm access control is in scope or explicitly out.",
                    severity="medium",
                    blocking=False,
                )
            ],
        ),
        LensScore(lens="best-practices", score=1.0),
        LensScore(lens="completeness", score=1.0),
    ]
    return PlanReview(plan_id="001-x", lenses=lenses, threshold=0.75).recompute()


def test_gate_refusal_names_the_failing_lens_and_finding():
    """The 409 must be actionable on its own: lens, score, threshold, finding (#387)."""
    review = _issue_387_review()
    assert review.gates_passed is False
    assert review.aggregate_score == pytest.approx(0.94)

    with pytest.raises(ApprovalError) as exc:
        approve(review, _plan(), approver="olaf")

    msg = str(exc.value)
    assert "security" in msg
    assert "0.70" in msg  # the failing score, not the aggregate
    assert "0.75" in msg  # the threshold it missed
    assert "No authentication/authorization criteria" in msg
    assert "medium" in msg
    assert "Confirm access control is in scope or explicitly out." in msg
    # And it must kill the wrong inference the aggregate invites.
    assert "0.94" in msg
    assert "aggregate is not the test" in msg


def test_gate_blockers_names_the_lens_that_blocks_approval():
    """A sub-threshold lens blocks even with no finding flagged blocking (#387)."""
    review = _issue_387_review()

    assert review.blocking_findings() == []  # no finding is flagged blocking
    assert [ls.lens for ls in review.gate_blockers()] == ["security"]


def test_gate_blockers_includes_a_lens_with_a_blocking_finding():
    lenses = [
        LensScore(lens="architecture", score=0.9),
        LensScore(
            lens="security",
            score=0.9,
            findings=[Finding(title="secret in plan", severity="critical", blocking=True)],
        ),
    ]
    review = PlanReview(plan_id="001-x", lenses=lenses).recompute()

    assert review.gates_passed is False
    assert [ls.lens for ls in review.gate_blockers()] == ["security"]

    with pytest.raises(ApprovalError) as exc:
        approve(review, _plan(), approver="olaf")
    assert "blocking finding" in str(exc.value)
    assert "secret in plan" in str(exc.value)


def test_gate_refusal_without_lenses_says_so():
    """An unreviewed plan fails the gate with no failing lens to name (#387)."""
    review = PlanReview(plan_id="001-x").recompute()

    with pytest.raises(ApprovalError) as exc:
        approve(review, _plan(), approver="olaf")
    assert "no lens scores" in str(exc.value)


def test_editing_plan_invalidates_approval():
    review = _passing_review()
    plan = _plan()
    approve(review, plan, approver="olaf")
    assert is_valid(review, plan) is True

    # Edit the plan's substance — changes the content hash.
    edited = plan.model_copy(
        update={"criteria": [*plan.criteria, Criterion(id="AC#2", text="Also this")]}
    ).with_hash()

    assert edited.content_hash != plan.content_hash
    assert is_valid(review, edited) is False  # hash mismatch

    revalidate(review, edited)
    assert review.human_approval.valid is False
    assert review.ready_to_emit() is False


def test_reject_leaves_unapproved():
    review = _passing_review()
    plan = _plan()

    reject(review, plan, approver="olaf", feedback="needs rework")

    assert review.human_approval.approved is False
    assert review.human_approval.valid is False
    assert review.human_approval.approved_by == "olaf"
    assert "needs rework" in review.human_approval.feedback
    assert review.human_approval.review_count == 1
    assert review.ready_to_emit() is False


def test_add_feedback_appends_line():
    review = _passing_review()

    add_feedback(review, author="reviewer", text="looks good", at="2026-06-03T00:00:00+00:00")

    assert review.human_approval.feedback == [
        "[2026-06-03T00:00:00+00:00] reviewer: looks good"
    ]


def test_json_round_trip_preserves_validity(tmp_path):
    review = _passing_review()
    plan = _plan()
    approve(review, plan, approver="olaf", feedback="approved")

    path = save_approval(review, tmp_path / "approval.json")
    loaded = load_approval(path)

    assert loaded.human_approval.approved is True
    assert loaded.human_approval.valid is True
    assert loaded.human_approval.plan_hash == plan.content_hash
    assert is_valid(loaded, plan) is True
    assert loaded.ready_to_emit() is True
