"""POST /api/plan/sessions/{id}/process must APPLY the body, not ignore it (#692).

This is the seam the original bug lived in: the client sent
{title, description, criteria}, FastAPI discarded it as an unmodelled body, and
the route returned 200 having re-run the untouched plan. Service-level tests
cannot see that — `update_plan` was never reached. So this asserts through the
route function itself.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from fastapi import HTTPException  # noqa: E402
from plan.service import PlanService  # noqa: E402
from server.routes import plan_pipeline as pp  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice.
## Acceptance Criteria
- User can request a refund through the API
- The endpoint requires a valid JWT and rejects unauthenticated callers
"""


@pytest.fixture
def service(monkeypatch):
    svc = PlanService(persist=False)
    monkeypatch.setattr(pp, "SERVICE", svc)
    return svc


def _seed(svc: PlanService) -> str:
    session = svc.ingest_text(_PLAN, title="Refund API")
    svc.process(session.session_id)
    return session.session_id


def test_process_applies_the_update_body(service):
    sid = _seed(service)

    result = asyncio.run(
        pp.process(
            sid,
            updates=pp.PlanUpdateBody(
                title="Refund API v2",
                description="Lawful basis: contract.",
                criteria=[{"id": "AC#1", "text": "Refunds require an approver"}],
            ),
        )
    )

    assert result["status"] == "processed"
    plan = service.get(sid).plan
    assert plan.title == "Refund API v2"
    assert plan.description == "Lawful basis: contract."
    assert [c.text for c in plan.criteria] == ["Refunds require an approver"]


def test_process_without_a_body_re_runs_unchanged(service):
    """The pre-existing bare re-run must keep working — no body, no edit."""
    sid = _seed(service)
    before = service.get(sid).plan.title

    result = asyncio.run(pp.process(sid))

    assert result["status"] == "processed"
    assert service.get(sid).plan.title == before


def test_review_returned_describes_the_submitted_text(service):
    """The returned review must be of the NEW text, not the old — the whole point."""
    sid = _seed(service)

    result = asyncio.run(
        pp.process(sid, updates=pp.PlanUpdateBody(description="revised wording"))
    )

    assert result["review"] is not None
    assert service.get(sid).plan.description == "revised wording"
    assert service.get(sid).status == "processed"


def test_a_malformed_criterion_is_400_not_500(service):
    """Was a bare KeyError escaping as a 500 (PR #696 review)."""
    sid = _seed(service)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            pp.process(sid, updates=pp.PlanUpdateBody(criteria=[{"text": "no id"}]))
        )

    assert caught.value.status_code == 400


def test_an_empty_title_is_400_and_an_unknown_session_is_404(service):
    """Validation must not masquerade as "not found" — both were 404."""
    sid = _seed(service)

    with pytest.raises(HTTPException) as bad_input:
        asyncio.run(pp.process(sid, updates=pp.PlanUpdateBody(title="   ")))
    assert bad_input.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        asyncio.run(pp.process("no-such-session", updates=pp.PlanUpdateBody(title="x")))
    assert missing.value.status_code == 404
