"""Attribution stamped by the three plan intake routes (#996)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

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

from plan.emit.docs.render import render_plan_docs  # noqa: E402
from plan.service import PlanService  # noqa: E402
from plan.service_helpers import agent_identity_for  # noqa: E402
from server.routes import plan_pipeline as pp  # noqa: E402

_PLAN = "# Widget service\nA FastAPI service.\n## Acceptance Criteria\n- Exposes widgets\n"
_USER = {"id": "u-1", "api_key_id": "k-1", "org_id": "o-1"}
_BOTH = {"originator": "u-1", "agent_identity": "agent:pfactory/k-1"}


@pytest.fixture(autouse=True)
def _no_project_tracking(monkeypatch) -> None:
    from server.routes import projects

    monkeypatch.setattr(projects, "ensure_tracked_project", lambda repo, org=None: None)


@pytest.fixture()
def service(monkeypatch) -> PlanService:
    svc = PlanService()
    monkeypatch.setattr(pp, "SERVICE", svc)

    async def _ingest_org(request, repo, db):
        return None

    monkeypatch.setattr(pp, "_ingest_org", _ingest_org)
    return svc


class _Request:
    def __init__(self, user: dict) -> None:
        self.headers = {}
        self.state = SimpleNamespace(user=user)


def test_jwt_user_uses_id_never_email() -> None:
    result = pp._attribution({"id": "u-1", "email": "a@b.c", "role": "user"})
    assert result == {"originator": "u-1", "agent_identity": None}
    assert "a@b.c" not in str(result)
    # No id: null, not the email (AIFactory falls back to it; PFactory must not).
    assert pp._attribution({"email": "a@b.c", "role": "user"})["originator"] is None


def test_key_user_has_both() -> None:
    assert pp._attribution(_USER) == _BOTH


def test_key_identity_uses_shared_format() -> None:
    assert pp._attribution(_USER)["agent_identity"] == agent_identity_for(_USER["api_key_id"])


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ({"id": "default", "is_service": True}, {"originator": None, "agent_identity": None}),
        (
            {"id": "default", "api_key_id": "k-1"},
            {"originator": None, "agent_identity": "agent:pfactory/k-1"},
        ),
        (
            {"id": "u-1", "is_service": True, "api_key_id": "k-1"},
            {"originator": None, "agent_identity": "agent:pfactory/k-1"},
        ),
    ],
)
def test_unknown_originator_keeps_independent_agent(user, expected) -> None:
    assert pp._attribution(user) == expected


def test_issue_author_is_not_the_caller() -> None:
    assert pp._attribution(_USER, authored_elsewhere=True) == {
        "originator": None,
        "agent_identity": "agent:pfactory/k-1",
    }


@pytest.mark.parametrize("value", [None, 7, "", "   ", "a\nb"])
def test_invalid_id_is_null(value) -> None:
    assert pp._attribution({"id": value, "api_key_id": "k-1"}) == {
        "originator": None,
        "agent_identity": "agent:pfactory/k-1",
    }


@pytest.mark.parametrize("value", [None, 7, "", "   ", "a\nb"])
def test_invalid_key_id_is_null(value) -> None:
    assert pp._attribution({"id": "u-1", "api_key_id": value}) == {
        "originator": "u-1",
        "agent_identity": None,
    }


def test_non_dict_and_surrounding_spaces() -> None:
    assert pp._attribution(None) == {"originator": None, "agent_identity": None}
    assert pp._attribution({"id": " u-1 ", "api_key_id": " k-1 "}) == {
        "originator": " u-1 ",
        "agent_identity": "agent:pfactory/ k-1 ",
    }


def test_text_route_stamps_both(service: PlanService) -> None:
    out = asyncio.run(pp.ingest_text(pp.IngestTextBody(text=_PLAN), _Request(_USER), db=None))
    session = service.get(out["session_id"])
    assert (session.originator, session.agent_identity) == ("u-1", "agent:pfactory/k-1")


def test_upload_route_stamps_both(service: PlanService) -> None:
    class _File:
        filename = "plan.md"

        async def read(self) -> bytes:
            return _PLAN.encode()

    out = asyncio.run(
        pp.ingest_upload(
            _Request(_USER),
            file=_File(),
            title=None,
            category="",
            template="",
            repo=None,
            base_ref=None,
            db=None,
        )
    )
    session = service.get(out["session_id"])
    assert (session.originator, session.agent_identity) == ("u-1", "agent:pfactory/k-1")


def test_issue_route_stamps_agent_only(service: PlanService) -> None:
    out = asyncio.run(
        pp.ingest_from_issue(
            pp.FromIssueBody(repo="acme/widgets", issue_number=996, body=_PLAN),
            _Request(_USER),
            db=None,
        )
    )
    session = service.get(out["session_id"])
    assert (session.originator, session.agent_identity) == (None, "agent:pfactory/k-1")


def test_authenticated_proxy_route_signs_principal_and_key(service) -> None:
    request = _Request({"id": "u-1", "api_key_id": "k-1"})
    out = asyncio.run(
        pp.ingest_text(pp.IngestTextBody(text=_PLAN, channel="cfactory"), request, db=None)
    )
    sig = render_plan_docs(service.process(out["session_id"])).run_signature
    assert sig["source_channel"] == "cfactory"
    assert (sig["originator"], sig["originator_basis"]) == ("u-1", "principal")
    assert (sig["agent_identity"], sig["agent_identity_basis"]) == (
        "agent:pfactory/k-1",
        "api-key",
    )
