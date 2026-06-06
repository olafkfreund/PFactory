"""Tests for execution.review_tier derivation (epic #65, child 6)."""

from __future__ import annotations

from plan.emit.review_tier import attach_review_tier, derive_review_tier
from plan.review.models import Finding, LensScore, PlanReview


def _review(*, score: float, passed: bool, blocking: bool = False) -> PlanReview:
    findings = [Finding(title="x", severity="critical", blocking=True)] if blocking else []
    return PlanReview(
        plan_id="001",
        lenses=[LensScore(lens="architecture", score=score, findings=findings, blocking=blocking)],
        aggregate_score=score,
        gates_passed=passed,
    )


def test_blocking_when_gates_failed() -> None:
    assert derive_review_tier(_review(score=0.95, passed=False)) == "blocking"


def test_blocking_when_blocking_finding() -> None:
    assert derive_review_tier(_review(score=0.95, passed=False, blocking=True)) == "blocking"


def test_auto_when_high_score_and_passed() -> None:
    assert derive_review_tier(_review(score=0.95, passed=True)) == "auto"


def test_async_when_middling_score() -> None:
    assert derive_review_tier(_review(score=0.8, passed=True)) == "async"


def test_attach_sets_execution_review_tier() -> None:
    contract: dict = {"execution": {"model": "sonnet"}}
    attach_review_tier(contract, _review(score=0.8, passed=True))
    assert contract["execution"]["review_tier"] == "async"


def test_attach_creates_execution_if_absent() -> None:
    contract: dict = {}
    attach_review_tier(contract, _review(score=0.95, passed=True))
    assert contract["execution"]["review_tier"] == "auto"
