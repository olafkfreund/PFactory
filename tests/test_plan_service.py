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
