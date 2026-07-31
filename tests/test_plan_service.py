"""Tests for PlanService — the full-pipeline orchestrator (#20 backend)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.service import PlanService, PlanServiceError  # noqa: E402

_SOFTWARE_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice with auth and a Kubernetes
Helm deploy.
## Acceptance Criteria
- User can request a refund through the API
- Refunds are written to the audit log
- The endpoint requires a valid JWT
"""


def test_ingest_then_process_runs_full_pipeline():
    svc = PlanService()
    session = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API")
    assert session.status == "ingested"
    assert session.plan.criteria  # parsed ACs

    processed = svc.process(session.session_id)
    assert processed.status == "processed"
    assert processed.plan.target_kind == "software"
    assert processed.plan.plan_type == "software-service"
    kinds = {c.kind for c in processed.epic.children}
    assert "feature" in kinds and "testing" in kinds and "cicd" in kinds
    assert {a.kind for a in processed.artifacts} == {"testing", "cicd"}
    assert processed.review is not None
    assert processed.epic.validate_dependencies() == []


def test_approve_then_emit_dry_run():
    svc = PlanService()
    sid = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API").session_id
    review = svc.process(sid).review
    assert review.gates_passed  # healthy plan passes

    svc.approve(sid, approver="olaf")
    session = svc.get(sid)
    assert session.status == "approved"
    assert session.review.ready_to_emit()

    emitted = svc.emit(sid, repo="olafkfreund/Demo", dry_run=True)
    assert emitted.emit_result["dry_run"] is True
    assert len(emitted.emit_result["planned"]) >= 1  # epic + children planned


def test_emit_refused_before_approval_when_not_dry_run():
    svc = PlanService()
    sid = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API").session_id
    svc.process(sid)
    emitted = svc.emit(sid, repo="olafkfreund/Demo", dry_run=False)
    # ungoverned (no approval) → emitter refuses, records an error, no status change
    assert emitted.emit_result["errors"]
    assert emitted.status != "emitted"


def test_list_and_unknown_session():
    svc = PlanService()
    svc.ingest_text(_SOFTWARE_PLAN, title="Refund API")
    assert len(svc.list_sessions()) == 1
    assert svc.list_sessions()[0]["status"] == "ingested"
    with pytest.raises(PlanServiceError):
        svc.get("999-nope")


def test_cannot_approve_before_process():
    svc = PlanService()
    sid = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API").session_id
    with pytest.raises(PlanServiceError):
        svc.approve(sid, approver="olaf")


# ── the cockpit needs WHY a plan is blocked, not just THAT it is (CFactory#245) ──


def _blocked_session():
    from plan.models import Criterion, NormalizedPlan
    from plan.review.models import Finding, LensScore, PlanReview
    from plan.service import PlanSession

    plan = NormalizedPlan(
        plan_id="027",
        title="VAT quote endpoint",
        description="d",
        source_format="markdown",
        criteria=[Criterion(id="AC#1", text="x")],
    )
    review = PlanReview(
        plan_id="027",
        threshold=0.75,
        lenses=[
            LensScore(
                lens="security",
                score=0.70,
                findings=[
                    Finding(title="No authentication/authorization criteria", severity="medium")
                ],
            ),
            LensScore(lens="clarity", score=1.0),
        ],
    ).recompute()
    return PlanSession(session_id="027", plan=plan, review=review)


def test_summary_carries_the_per_lens_verdict():
    """`gates_passed` alone cannot disable a button with a reason attached."""
    out = _blocked_session().summary()
    assert out["gates_passed"] is False

    review = out["review"]
    assert review["threshold"] == 0.75
    lenses = {ls["lens"]: ls for ls in review["lenses"]}
    assert lenses["security"]["score"] == 0.70
    assert lenses["security"]["findings"][0]["title"] == "No authentication/authorization criteria"
    assert lenses["clarity"]["findings"] == []


def test_summary_aggregate_is_carried_but_is_not_the_verdict():
    """The aggregate clears the threshold while the plan is still blocked.

    This is the exact shape that confused a real approval: 'lens security scored
    0.70, below the 0.75 threshold ... the 0.94 aggregate is not the test'. A
    consumer reading the aggregate as the verdict would disagree with the server.
    """
    review = _blocked_session().summary()["review"]
    assert review["aggregate_score"] > review["threshold"]
    assert review["gates_passed"] is False


def test_summary_review_is_none_before_the_gates_run():
    """An un-reviewed session must serialise unchanged for existing consumers."""
    from plan.models import NormalizedPlan
    from plan.service import PlanSession

    plan = NormalizedPlan(plan_id="028", title="t", description="d", source_format="markdown")
    out = PlanSession(session_id="028", plan=plan).summary()
    assert out["review"] is None
    assert out["gates_passed"] is None
