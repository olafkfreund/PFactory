"""RBAC coverage for the rmux console and stdio MCP proxies."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Request
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
            db.add(models.Organization(id="B", name="B", slug="B", owner_id="owner"))
            await db.flush()
            db.add(models.OrgMember(org_id="A", user_id="viewer-a", role="viewer"))
            db.add(models.OrgMember(org_id="A", user_id="member-a", role="member"))
            await db.commit()

    asyncio.run(_init())
    audits = []

    async def _capture(**kw):
        audits.append(kw)

    monkeypatch.setattr(pa.audit_service, "log_audit_event_bg", _capture)
    monkeypatch.setattr(
        pa.project_paths,
        "load_projects",
        lambda: {"p1": {"org_id": "A"}, "pB": {"org_id": "B"}},
    )
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
    bridge.get_registry()._states.pop("sB", None)
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


@pytest.mark.parametrize("action", ["attach", "detach"])
def test_console_rejects_cross_project_session(env, tmp_path, action):
    client, settings, _, bridge = env
    bridge.get_registry()._states["sB"] = bridge.SessionState(
        spec_id="sB",
        session_name="pfactory-task-sB",
        fifo_path=tmp_path / "sB.fifo",
        project_id="pB",
    )
    settings.RBAC_ENFORCE_TASK_ROUTES = True
    for spec_id, expected in (("p1:sB", 404), ("pB:sB", 403)):
        response = client.post(
            f"/api/tasks/{spec_id}/agent-console/{action}",
            json={"connection_id": "c1"},
            headers={"X-U": "member-a"},
        )
        assert response.status_code == expected


def test_console_websocket_rejects_cross_project_session(env, monkeypatch, tmp_path):
    client, settings, _, bridge = env
    bridge.get_registry()._states["sB"] = bridge.SessionState(
        spec_id="sB",
        session_name="pfactory-task-sB",
        fifo_path=tmp_path / "sB.fifo",
        project_id="pB",
    )
    monkeypatch.setattr(
        bridge, "authenticate_websocket", AsyncMock(return_value={"id": "member-a"})
    )
    settings.RBAC_ENFORCE_TASK_ROUTES = True
    for spec_id, expected in (("p1:sB", 4004), ("pB:sB", 4003)):
        with (
            client.websocket_connect(f"/api/tasks/{spec_id}/agent-console/ws") as ws,
            pytest.raises(WebSocketDisconnect) as exc,
        ):
            ws.receive_text()
        assert exc.value.code == expected


def test_bare_session_requires_service_principal(env, tmp_path):
    _, _, _, bridge = env
    state = bridge.SessionState(
        spec_id="sB",
        session_name="pfactory-task-sB",
        fifo_path=tmp_path / "sB.fifo",
        project_id="pB",
    )
    bridge.get_registry()._states["sB"] = state
    with pytest.raises(HTTPException) as exc:
        bridge._resolve_state_or_404("sB", {"id": "member-a"})
    assert exc.value.status_code == 404
    assert bridge._resolve_state_or_404("sB", {"id": "service", "is_service": True}) is state
    state.project_id = None
    with pytest.raises(HTTPException) as exc:
        bridge._resolve_state_or_404("sB", {"id": "member-a"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_for_task_records_project_id(tmp_path):
    session = importlib.import_module("server.rmux.session")
    registry = session.SessionRegistry(wrapper=AsyncMock(), panes_dir=tmp_path / "panes")
    await registry.create_for_task("sB", tmp_path, "true", project_id="pB")
    assert registry.get_state("sB").project_id == "pB"


@pytest.fixture
def mcp_env(env, monkeypatch):
    _, settings, audits, bridge = env
    proxy = importlib.import_module("server.mcp_stdio.router")
    execution = importlib.import_module("server.routes.execution")
    auth = importlib.import_module("server.mcp_remote.auth")
    legacy = importlib.import_module("server.mcp_stdio.auth")._LegacyAdminKey()
    key = auth.AuthenticatedKey(
        key_id="key-b",
        scopes=frozenset({"task:write", "plan:write"}),
        user_id="member-a",
        org_id="B",
    )
    selected = {"key": key}
    app = FastAPI()
    app.include_router(proxy.router)

    async def _db():
        async with bridge.async_session_factory() as db:
            yield db

    app.dependency_overrides[proxy.get_db] = _db
    for route in app.routes:
        if route.path in {
            "/api/mcp-stdio/tasks/{task_id}/start",
            "/api/mcp-stdio/plan/sessions/{session_id}/process",
        }:
            for dependency in route.dependant.dependencies:
                if dependency.call.__closure__:
                    app.dependency_overrides[dependency.call] = lambda: selected["key"]

    handler = AsyncMock(return_value={"ok": True})
    plan_handler = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(execution, "start_task", handler)
    monkeypatch.setattr(proxy, "plan_process", plan_handler)
    monkeypatch.setattr(proxy, "log_audit_event_bg", AsyncMock())
    return TestClient(app), settings, audits, selected, legacy, handler, plan_handler


@pytest.mark.parametrize(
    ("path", "handler_index"),
    [
        ("/api/mcp-stdio/tasks/p1:s/start", 5),
        ("/api/mcp-stdio/plan/sessions/s/process", 6),
    ],
)
def test_mcp_write_requires_member(mcp_env, path, handler_index):
    client, settings, audits, selected, legacy, _, _ = mcp_env
    handler = mcp_env[handler_index]

    settings.RBAC_ENFORCE_TASK_ROUTES = True
    response = client.post(path)
    assert response.status_code == 403
    handler.assert_not_awaited()

    settings.RBAC_ENFORCE_TASK_ROUTES = False
    response = client.post(path)
    assert response.status_code == 200
    handler.assert_awaited_once()
    assert len(audits) == 2
    assert audits[1]["details"]["shadow"] is True

    selected["key"] = legacy
    response = client.post(path)
    assert response.status_code == 200
    assert len(audits) == 2
