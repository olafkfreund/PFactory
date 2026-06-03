"""Tests for ``apps/web-server/server/routes/capabilities.py``.

Endpoint contract:

  GET /api/capabilities → 200 {"rmux": bool}

  - Always mounted (no flag gate)
  - Returns the current ``PFACTORY_RMUX_ENABLED`` state
  - Frontend uses this to decide whether to show the Live Console tab
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_capabilities() -> FastAPI:
    """Minimal FastAPI app mounting only the capabilities route."""
    from server.routes.capabilities import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app_with_capabilities) -> TestClient:
    return TestClient(app_with_capabilities)


class TestCapabilities:
    def test_endpoint_responds_200(self, client: TestClient) -> None:
        r = client.get("/api/capabilities")
        assert r.status_code == 200

    def test_response_shape(self, client: TestClient) -> None:
        r = client.get("/api/capabilities")
        body = r.json()
        assert "rmux" in body
        assert isinstance(body["rmux"], bool)

    def test_rmux_false_by_default(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.delenv("PFACTORY_RMUX_ENABLED", raising=False)
        r = client.get("/api/capabilities")
        assert r.json()["rmux"] is False

    def test_rmux_true_when_flag_set(
        self, client: TestClient, monkeypatch
    ) -> None:
        monkeypatch.setenv("PFACTORY_RMUX_ENABLED", "true")
        r = client.get("/api/capabilities")
        assert r.json()["rmux"] is True
