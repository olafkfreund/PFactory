"""Tests for the ReadinessReport / Waiver arithmetic (epic #33, P0.1)."""

from __future__ import annotations

from plan.models import NormalizedPlan
from plan.review.readiness.models import (
    ReadinessCheckResult,
    ReadinessReport,
    Waiver,
)


def _plan(title: str = "Build a thing", description: str = "do it") -> NormalizedPlan:
    plan = NormalizedPlan(
        plan_id="001-thing", title=title, description=description, source_format="markdown"
    )
    return plan.with_hash()


def _fail(check_id: str, *, hard: bool = True, waivable: bool = True) -> ReadinessCheckResult:
    return ReadinessCheckResult(
        check_id=check_id, title=check_id, status="fail", severity="high",
        hard=hard, waivable=waivable,
    )


def test_hard_failures_lists_only_hard_fails() -> None:
    report = ReadinessReport(
        plan_id="001-thing",
        results=[
            _fail("a"),
            _fail("b", hard=False),  # advisory fail — not a hard failure
            ReadinessCheckResult(check_id="c", title="c", status="pass", hard=True),
        ],
    )
    assert [r.check_id for r in report.hard_failures()] == ["a"]


def test_unwaived_and_is_ready_without_waiver() -> None:
    report = ReadinessReport(plan_id="001-thing", results=[_fail("a")])
    assert not report.is_ready()
    assert [r.check_id for r in report.unwaived_hard_failures()] == ["a"]


def test_valid_waiver_clears_hard_failure() -> None:
    plan = _plan()
    report = ReadinessReport(
        plan_id="001-thing", plan_hash=plan.content_hash, results=[_fail("a")]
    )
    report.waivers.append(
        Waiver(check_ids=["a"], reason="known", waived_by="olaf", plan_hash=plan.content_hash)
    )
    assert report.is_ready(plan)
    assert report.unwaived_hard_failures(plan) == []


def test_waiver_invalidated_when_plan_edited() -> None:
    plan = _plan()
    report = ReadinessReport(
        plan_id="001-thing", plan_hash=plan.content_hash, results=[_fail("a")]
    )
    report.waivers.append(
        Waiver(check_ids=["a"], reason="known", waived_by="olaf", plan_hash=plan.content_hash)
    )
    assert report.is_ready(plan)

    edited = _plan(description="something materially different")
    report.revalidate(edited)
    assert not report.waivers[0].valid
    assert not report.is_ready(edited)


def test_one_waiver_covers_multiple_checks() -> None:
    plan = _plan()
    report = ReadinessReport(
        plan_id="001-thing", plan_hash=plan.content_hash,
        results=[_fail("a"), _fail("b")],
    )
    report.waivers.append(
        Waiver(check_ids=["a", "b"], reason="batch", waived_by="olaf", plan_hash=plan.content_hash)
    )
    assert report.is_ready(plan)


def test_round_trip_serialization() -> None:
    report = ReadinessReport(plan_id="001-thing", results=[_fail("a")])
    restored = ReadinessReport.model_validate_json(report.model_dump_json())
    assert restored.hard_failures()[0].check_id == "a"
