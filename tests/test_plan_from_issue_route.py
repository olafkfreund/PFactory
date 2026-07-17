"""RFC-0011 hard tier: POST /api/plan/sessions/from-issue (AIFactory#874).

The door AIFactory's intake poller routes ``factory:hard`` issues through. What
matters here is not that a session appears, but that the ORIGIN issue (the one a
human filed) stays the correlation key all the way through emit — the epic
PFactory creates must not silently take over the chain, which is exactly what
``correlation_key_for`` did before #874.
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
from plan.completion import build_completion_event, correlation_key_for  # noqa: E402
from plan.service import PlanService  # noqa: E402
from server.routes import plan_pipeline as pp  # noqa: E402

# The exact payload AIFactory's intake_poller._route_hard sends.
_POLLER_PAYLOAD = {
    "repo": "olafkfreund/AIFactory",
    "provider": "github",
    "issue_number": 874,
    "title": "Refund flow",
    "body": (
        "# Refund flow\n"
        "Add a refund flow to the orders web app.\n"
        "## Acceptance Criteria\n"
        "- A finance user can issue a refund\n"
        "- The order status becomes refunded\n"
    ),
    "labels": ["factory:hard"],
    "autonomy_tier": "hard",
    "change_mode": "existing",
}


@pytest.fixture(autouse=True)
def _no_project_tracking(monkeypatch) -> None:
    """Stub the tracked-project side effect (it writes through to the DB)."""
    from server.routes import projects

    monkeypatch.setattr(projects, "ensure_tracked_project", lambda repo: None)


@pytest.fixture()
def service(monkeypatch) -> PlanService:
    """Route the module-level SERVICE at a fresh in-memory PlanService."""
    svc = PlanService()
    monkeypatch.setattr(pp, "SERVICE", svc)
    return svc


class _Request:
    """Header-carrying Request stand-in for direct route calls (#308)."""

    def __init__(self, headers: dict | None = None) -> None:
        self.headers = headers or {}


def _post(payload: dict) -> dict:
    return asyncio.run(pp.ingest_from_issue(pp.FromIssueBody(**payload), _Request()))


def test_accepts_poller_shape_and_creates_a_session(service: PlanService) -> None:
    out = _post(_POLLER_PAYLOAD)

    assert out["session_id"]
    assert out["status"] == "ingested"
    # Reached the real service, not just a parsed plan.
    assert service.get(out["session_id"]).plan.title


def test_ingest_reuses_the_github_issue_channel(service: PlanService) -> None:
    """An issue is just another intake channel — no parallel ingest stack."""
    out = _post(_POLLER_PAYLOAD)

    assert service.get(out["session_id"]).plan.source_channel == "github_issue"


def test_origin_issue_number_is_retrievable_from_the_session(
    service: PlanService,
) -> None:
    out = _post(_POLLER_PAYLOAD)
    session = service.get(out["session_id"])

    assert session.origin_issue_number == 874
    assert session.repo == "olafkfreund/AIFactory"
    # ...and is exposed on the wire, not just in memory.
    assert out["origin_issue_number"] == 874
    assert session.summary()["origin_issue_number"] == 874


def test_correlation_key_is_the_origin_issue_from_ingest(service: PlanService) -> None:
    """The key exists immediately — not only once the plan reaches emit."""
    out = _post(_POLLER_PAYLOAD)

    assert out["correlation_key"] == "olafkfreund/AIFactory#874"


def test_autonomy_tier_is_carried_onto_the_plan(service: PlanService) -> None:
    out = _post(_POLLER_PAYLOAD)

    assert service.get(out["session_id"]).plan.autonomy_tier == "hard"


def test_the_emitted_epic_does_not_hijack_the_correlation_key(
    service: PlanService,
) -> None:
    """The regression #874 is really about.

    Emit sets ``emitted_issue_number`` to the epic PFactory creates and
    re-derives the key. Before #874 that overwrote the origin, silently
    re-pointing the chain at PFactory's own output.
    """
    out = _post(_POLLER_PAYLOAD)
    session = service.get(out["session_id"])

    session.emitted_issue_number = 4242  # the epic PFactory would create

    assert correlation_key_for(session) == "olafkfreund/AIFactory#874"
    assert build_completion_event(session)["correlation_key"] == ("olafkfreund/AIFactory#874")


@pytest.mark.parametrize(
    ("body", "expected_detail"),
    [
        pytest.param("", "empty source", id="empty_body"),
        pytest.param(
            "Please add a refund flow, thanks.",
            "acceptance criteria",
            id="prose_without_criteria",
        ),
    ],
)
def test_issue_without_acceptance_criteria_is_a_400_not_a_500(
    service: PlanService, body: str, expected_detail: str
) -> None:
    """An unplannable issue must refuse cleanly, and say why.

    The poller turns a 4xx into a terminal error it comments back on the issue,
    so the detail is what the human filing the issue ends up reading.
    """
    with pytest.raises(HTTPException) as exc:
        _post(dict(_POLLER_PAYLOAD, body=body))

    assert exc.value.status_code == 400
    assert expected_detail in str(exc.value.detail).lower()


def test_missing_required_fields_are_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        pp.FromIssueBody(title="no repo, no issue number")


def test_non_issue_sessions_keep_the_pre_874_key(service: PlanService) -> None:
    """No origin => emitted epic still wins, then the synthetic fallback."""
    session = service.ingest_text(_POLLER_PAYLOAD["body"], title="Portal plan")

    assert session.origin_issue_number is None
    assert correlation_key_for(session) == f"pf-{session.session_id}"

    session.emitted_issue_number = 4242
    assert correlation_key_for(session) == "4242"
