"""POST /api/contract-events stores the feedback on the plan session (#998)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent / "apps"
for p in (_ROOT / "backend", _ROOT / "web-server"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

pytest.importorskip("fastapi")
pytest.importorskip("yaml")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from plan.service import SERVICE, StaleSessionError  # noqa: E402
from server.routes import contract_events  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(contract_events.router)
    return TestClient(app)


def test_event_is_recorded_on_the_session(monkeypatch):
    calls = []
    monkeypatch.setattr(SERVICE, "record_downstream", lambda p, **_: calls.append(p) or True)
    r = _client().post(
        "/api/contract-events",
        json={"correlation_key": "fb-1", "service": "aifactory", "status": "failed"},
    )
    assert r.status_code == 200 and calls[0]["correlation_key"] == "fb-1"


def test_persistent_stale_session_is_503_so_the_emitter_retries(monkeypatch):
    def stale(_, **__):
        raise StaleSessionError("moved on")

    monkeypatch.setattr(SERVICE, "record_downstream", stale)
    r = _client().post(
        "/api/contract-events",
        json={"correlation_key": "fb-2", "service": "aifactory", "status": "failed"},
    )
    assert r.status_code == 503


def test_tenant_comes_from_the_body_when_multi_tenant(monkeypatch):
    seen = []
    monkeypatch.setattr(SERVICE, "record_downstream", lambda p, **kw: seen.append(kw) or True)
    monkeypatch.setenv("PFACTORY_MULTI_TENANT", "1")
    _client().post(
        "/api/contract-events",
        json={
            "correlation_key": "fb-3",
            "service": "aifactory",
            "status": "failed",
            "tenant_id": "acme",
        },
    )
    monkeypatch.delenv("PFACTORY_MULTI_TENANT")
    _client().post(
        "/api/contract-events",
        json={
            "correlation_key": "fb-4",
            "service": "aifactory",
            "status": "failed",
            "tenant_id": "acme",
        },
    )
    assert [k["tenant_id"] for k in seen] == ["acme", None]
