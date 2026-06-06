"""PlanService.waive — exposing the readiness waiver through the live flow (#77)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.review.models import PlanReview  # noqa: E402
from plan.review.readiness.checks import run_readiness  # noqa: E402
from plan.review.readiness.waiver import WaiverError  # noqa: E402
from plan.service import PlanService, PlanServiceError  # noqa: E402

_PLAN = """# Widget service
A FastAPI service tested with pytest.
## Acceptance Criteria
- user can log in
"""


def _session_with_uncovered_ac(svc: PlanService) -> str:
    """Ingest + attach a passing-gates review whose readiness has a waivable
    hard failure (an AC with no child covering it → ac-child-coverage)."""
    session = svc.ingest_text(_PLAN, title="Widget service")
    # epic with a child that does NOT cover the "user can log in" AC.
    session.epic = EpicPlan(
        plan_id=session.plan.plan_id,
        epic_title="Widget service",
        children=[ChildIssue(key="C1", title="unrelated")],
    )
    review = PlanReview(plan_id=session.plan.plan_id, gates_passed=True)
    review.readiness = run_readiness(session.plan, session.epic)
    session.review = review
    return session.session_id


def test_waive_clears_hard_failure() -> None:
    svc = PlanService()
    sid = _session_with_uncovered_ac(svc)
    session = svc.get(sid)
    assert not session.review.readiness.is_ready(session.plan)

    out = svc.waive(sid, check_ids=["ac-child-coverage"],
                    reason="tracked elsewhere", waived_by="olaf")
    assert out.review.readiness.is_ready(out.plan)


def test_waive_serialized_in_session_dump() -> None:
    svc = PlanService()
    sid = _session_with_uncovered_ac(svc)
    out = svc.waive(sid, check_ids=["ac-child-coverage"], reason="ok",
                    waived_by="olaf")
    dumped = out.model_dump()
    # readiness (with the recorded waiver) survives serialization for the portal.
    waivers = dumped["review"]["readiness"]["waivers"]
    assert waivers and waivers[0]["check_ids"] == ["ac-child-coverage"]


def test_waive_refused_on_passing_check() -> None:
    svc = PlanService()
    sid = _session_with_uncovered_ac(svc)
    with pytest.raises(WaiverError, match="not a hard failure"):
        svc.waive(sid, check_ids=["criteria-present"], reason="x", waived_by="olaf")


def test_waive_refused_on_unknown_check() -> None:
    svc = PlanService()
    sid = _session_with_uncovered_ac(svc)
    with pytest.raises(WaiverError, match="unknown"):
        svc.waive(sid, check_ids=["does-not-exist"], reason="x", waived_by="olaf")


def test_waive_before_process_raises_service_error() -> None:
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Widget service")
    with pytest.raises(PlanServiceError, match="process the plan before waiving"):
        svc.waive(session.session_id, check_ids=["ac-child-coverage"],
                  reason="x", waived_by="olaf")
