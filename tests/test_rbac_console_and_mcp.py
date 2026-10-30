"""Step 3 RBAC coverage for the rmux console."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.websockets import WebSocketDisconnect

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))


@pytest.fixture
def env(tmp_path, monkeypatch):
    models = importlib.import_module("server.database.models")
    bridge = importlib.import_module("server.rmux.bridge")
    pa = importlib.import_module("server.routes.project_authz")

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(models.Base.metadata.create_all)
        async with session_local() as db:
            for uid in ("viewer-a", "member-a", "owner"):
                db.add(models.User(id=uid, email=f"{uid}@x.test", password_hash=uid))
            await db.flush()
            db.add(models.Organization(id="A", name="A", slug="A", owner_id="owner"))
            await db.flush()
            db.add(models.OrgMember(org_id="A", user_id="viewer-a", role="viewer"))
            db.add(models.OrgMember(org_id="A", user_id="member-a", role="member"))
            await db.commit()

    asyncio.run(_init())
    audits = []

    async def _capture(**kw):
        audits.append(kw)

    monkeypatch.setattr(pa.audit_service, "log_audit_event_bg", _capture)
    monkeypatch.setattr(pa.project_paths, "load_projects", lambda: {"p1": {"org_id": "A"}})
    settings = SimpleNamespace(DISABLE_AUTH=False, RBAC_ENFORCE_TASK_ROUTES=False)
    monkeypatch.setattr(pa, "get_settings", lambda: settings)
    monkeypatch.setattr(bridge, "async_session_factory", session_local)

    app = FastAPI()
    app.include_router(bridge.router)

    async def _db():
        async with session_local() as db:
            yield db

    app.dependency_overrides[bridge.get_db] = _db

    @app.middleware("http")
    async def _principal(request: Request, call_next):
        request.state.user = {"id": request.headers.get("X-U", "viewer-a")}
        return await call_next(request)

    yield TestClient(app), settings, audits, bridge
    asyncio.run(engine.dispose())


@pytest.mark.parametrize(
    ("spec_id", "project_id"),
    [("p1:s1", "p1"), ("p1:s:2", "p1"), ("s1", None)],
)
def test_project_of(spec_id, project_id):
    bridge = importlib.import_module("server.rmux.bridge")
    assert bridge._project_of(spec_id) == project_id


@pytest.mark.parametrize("action", ["attach", "detach"])
def test_console_write_requires_member(env, action):
    client, settings, audits, _ = env
    path = f"/api/tasks/p1:s1/agent-console/{action}"
    response = client.post(path, json={"connection_id": "c1"}, headers={"X-U": "viewer-a"})
    assert response.status_code == 404  # shadow reaches the unregistered-session handler
    assert len(audits) == 1
    assert audits[0]["details"]["shadow"] is True
    settings.RBAC_ENFORCE_TASK_ROUTES = True
    response = client.post(path, json={"connection_id": "c1"}, headers={"X-U": "viewer-a"})
    assert response.status_code == 403
    assert len(audits) == 2


def test_console_websocket_requires_viewer_before_session_lookup(env, monkeypatch):
    client, settings, audits, bridge = env
    monkeypatch.setattr(
        bridge, "authenticate_websocket", AsyncMock(return_value={"id": "outsider"})
    )
    settings.RBAC_ENFORCE_TASK_ROUTES = True
    with (
        client.websocket_connect("/api/tasks/p1:s1/agent-console/ws") as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        ws.receive_text()
    assert exc.value.code == 4003
    assert len(audits) == 1


def test_console_websocket_legacy_principal_reaches_session_lookup(env, monkeypatch):
    client, settings, audits, bridge = env
    monkeypatch.setattr(bridge, "authenticate_websocket", AsyncMock(return_value=None))
    settings.RBAC_ENFORCE_TASK_ROUTES = True
    with (
        client.websocket_connect("/api/tasks/p1:s1/agent-console/ws") as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        ws.receive_text()
    assert exc.value.code == 4004
    assert audits == []
