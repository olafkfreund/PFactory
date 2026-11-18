"""ingest-text accepts the CFactory source channel and logs rejections safely (#1053)."""

from __future__ import annotations

import asyncio
import logging
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

from fastapi import HTTPException  # noqa: E402
from plan.service import PlanService  # noqa: E402
from server.routes import plan_pipeline as pp  # noqa: E402

_PLAN = "# Widget service\nA FastAPI service.\n## Acceptance Criteria\n- Exposes widgets\n"
_SENTINEL = "SENTINEL-secret-token-123"


@pytest.fixture()
def service(monkeypatch) -> PlanService:
    from server.routes import projects

    monkeypatch.setattr(projects, "ensure_tracked_project", lambda repo, org=None: None)
    svc = PlanService()
    monkeypatch.setattr(pp, "SERVICE", svc)

    async def _ingest_org(request, repo, db):
        return None

    monkeypatch.setattr(pp, "_ingest_org", _ingest_org)
    return svc


def _ingest(channel: str, text: str = _PLAN):
    request = SimpleNamespace(headers={}, state=SimpleNamespace(user=None))
    body = pp.IngestTextBody(text=text, channel=channel)
    return asyncio.run(pp.ingest_text(body, request, db=None))


def test_cfactory_channel_is_accepted_and_recorded(service) -> None:
    out = _ingest("cfactory")
    session = service.get(out["session_id"])
    assert session.plan.source_channel == "cfactory"
    assert session.plan.content_trust == "trusted"


def test_unknown_channel_is_400_sanitised_and_logged_without_input(service, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=pp.logger.name):
        with pytest.raises(HTTPException) as err:
            _ingest(_SENTINEL, text=_PLAN + _SENTINEL)
    assert err.value.status_code == 400
    assert err.value.detail == "invalid request"
    assert "ingest-text rejected" in caplog.text
    assert "source_channel" in caplog.text
    assert _SENTINEL not in caplog.text


def test_non_pydantic_value_error_is_logged_class_only(service, monkeypatch, caplog) -> None:
    def _boom(*a, **k):
        raise ValueError(f"bad {_SENTINEL}")

    monkeypatch.setattr(service, "ingest_text", _boom)
    with caplog.at_level(logging.WARNING, logger=pp.logger.name):
        with pytest.raises(HTTPException) as err:
            _ingest("cfactory")
    assert err.value.status_code == 400
    assert "ValueError" in caplog.text
    assert _SENTINEL not in caplog.text
