"""PlanService.emit_contract — wiring the Task Contract v2 into the live flow (#65)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.review.gates import run_gates  # noqa: E402
from plan.review.models import LensScore, PlanReview  # noqa: E402
from plan.service import PlanService, PlanServiceError  # noqa: E402

_PLAN = """# Widget service
A FastAPI service tested with pytest.
## Acceptance Criteria
- exposes a widget API
"""


def _processed_session(svc: PlanService) -> str:
    """Ingest + attach a governed epic/review without the yaml-dependent process()."""
    session = svc.ingest_text(_PLAN, title="Widget service")
    session.epic = EpicPlan(
        plan_id=session.plan.plan_id,
        epic_title="Widget service",
        children=[
            ChildIssue(key="C1", title="Scaffold", kind="infra"),
            ChildIssue(
                key="C2",
                title="API",
                kind="feature",
                depends_on=["C1"],
                acceptance_criteria=["exposes a widget API"],
            ),
        ],
    )
    session.review = PlanReview(
        plan_id=session.plan.plan_id,
        lenses=[LensScore(lens="architecture", score=0.95)],
        aggregate_score=0.95,
        gates_passed=True,
    )
    return session.session_id


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url, *, params, json):
        self.calls.append({"url": url, "params": params, "json": json})
        return {"taskId": "t-9", "status": "accepted"}


def test_emit_contract_requires_epic() -> None:
    svc = PlanService()
    session = svc.ingest_text(_PLAN, title="Widget")
    with pytest.raises(PlanServiceError, match="process the plan"):
        svc.emit_contract(session.session_id)


def test_emit_contract_dry_run() -> None:
    svc = PlanService()
    sid = _processed_session(svc)
    out = svc.emit_contract(sid, repo="acme/widget", dry_run=True)
    assert out.contract_result["ok"] and out.contract_result["dry_run"]
    contract = out.contract_result["contract"]
    assert contract["contract_version"] == "2"
    assert contract["execution"]["review_tier"] == "auto"
    assert "unit" in contract["tfactory"]["lanes"]
    assert out.status != "emitted"  # dry-run doesn't advance status


def test_emit_contract_live_sets_correlation() -> None:
    svc = PlanService()
    sid = _processed_session(svc)
    http = FakeHttp()
    out = svc.emit_contract(
        sid, repo="acme/widget", project_id="p1", http=http, key="secret", dry_run=False
    )
    assert out.contract_result["ok"] and not out.contract_result["dry_run"]
    assert out.status == "emitted"
    assert out.aifactory_task_id == "t-9"
    assert http.calls[0]["url"].endswith("/api/tasks/from-plan")
    # signed
    assert http.calls[0]["json"]["plan"]["approval"]["signature"]


def test_emit_contract_live_refused_on_unwaived_hard_readiness() -> None:
    """#326: the gate that blocks approve must block the contract fast path too."""
    svc = PlanService()
    sid = _processed_session(svc)
    session = svc.get(sid)
    # Drop the child covering AC#1 -> ac-child-coverage hard-fails, unwaived.
    session.epic = EpicPlan(
        plan_id=session.plan.plan_id,
        epic_title="Widget service",
        children=[ChildIssue(key="C1", title="Scaffold", kind="infra")],
    )
    session.review = run_gates(session.plan, session.epic)
    session.review.gates_passed = True  # isolate readiness from lens scoring
    assert session.review.readiness.unwaived_hard_failures(session.plan)

    with pytest.raises(PlanServiceError, match="unwaived hard readiness"):
        svc.emit_contract(
            sid, repo="acme/widget", project_id="p1", http=FakeHttp(), key="secret", dry_run=False
        )
    # dry-run stays open — discovery must still work.
    assert svc.emit_contract(sid, dry_run=True).contract_result["dry_run"]
