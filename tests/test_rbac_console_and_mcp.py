"""RBAC coverage for the rmux console and stdio MCP proxies."""

from __future__ import annotations

import asyncio
import importlib
import json
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
        request.state.user = {
            "id": request.headers.get("X-U", "viewer-a"),
            "org_id": request.headers.get("X-Org", "A"),
        }
        return await call_next(request)

    yield TestClient(app), settings, audits, bridge
    bridge.get_registry()._states.pop("sB", None)
    bridge.get_registry()._states.pop("s1", None)
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
    tasks = importlib.import_module("server.routes.tasks")
    projects = importlib.import_module("server.routes.projects")
    auth = importlib.import_module("server.mcp_remote.auth")
    legacy = importlib.import_module("server.mcp_stdio.auth")._LegacyAdminKey()
    key = auth.AuthenticatedKey(
        key_id="key-b",
        scopes=frozenset({"mcp:read", "task:write", "plan:write"}),
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
        if hasattr(route, "dependant"):
            for dependency in route.dependant.dependencies:
                if dependency.call.__closure__:
                    app.dependency_overrides[dependency.call] = lambda: selected["key"]

    monkeypatch.setattr(
        proxy.project_paths,
        "load_projects",
        lambda: {"p1": {"org_id": "A"}, "pB": {"org_id": "B"}},
    )
    handler = AsyncMock(return_value={"ok": True})
    plan_handler = AsyncMock(return_value={"ok": True})
    read_handler = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(execution, "start_task", handler)
    monkeypatch.setattr(execution, "get_task_status", read_handler)
    monkeypatch.setattr(
        execution,
        "get_running_tasks",
        AsyncMock(return_value=execution.RunningTasksResponse(tasks=["p1:s1", "pB:sB"], count=2)),
    )
    monkeypatch.setattr(tasks, "get_task", read_handler)
    monkeypatch.setattr(tasks, "get_task_logs", read_handler)
    monkeypatch.setattr(tasks, "get_worktree_diff", read_handler)
    monkeypatch.setattr(
        tasks,
        "list_tasks",
        AsyncMock(
            return_value=tasks.TaskList(
                tasks=[
                    tasks.Task(
                        id=f"{pid}:s",
                        spec_id="s",
                        project_id=pid,
                        title=pid,
                        description=pid,
                        created_at="2026-01-01T00:00:00Z",
                        updated_at="2026-01-01T00:00:00Z",
                    )
                    for pid in ("p1", "pB")
                ],
                total=2,
            )
        ),
    )
    monkeypatch.setattr(
        projects,
        "list_projects",
        AsyncMock(return_value=[{"id": "p1"}, {"id": "pB"}]),
    )
    monkeypatch.setattr(proxy, "plan_process", plan_handler)
    monkeypatch.setattr(proxy, "plan_list_sessions", read_handler)
    monkeypatch.setattr(proxy, "plan_get_session", read_handler)
    monkeypatch.setattr(proxy, "plan_audit_pack", read_handler)
    monkeypatch.setattr(proxy, "log_audit_event_bg", AsyncMock())
    return (
        TestClient(app),
        settings,
        audits,
        selected,
        legacy,
        handler,
        plan_handler,
        read_handler,
        auth.AuthenticatedKey,
    )


def test_mcp_write_requires_member(mcp_env):
    client, settings, audits, selected, legacy, handler, plan_handler, _, key_cls = mcp_env

    # Org-B key on org-A task write: always 403 even with flag off (Deviation).
    settings.RBAC_ENFORCE_TASK_ROUTES = False
    response = client.post("/api/mcp-stdio/tasks/p1:s/start")
    assert response.status_code == 403
    handler.assert_not_awaited()
    assert len(audits) == 1

    # Plan write with org-B key (user has no membership in B): shadow 200, enforce 403.
    response = client.post("/api/mcp-stdio/plan/sessions/s/process")
    assert response.status_code == 200
    plan_handler.assert_awaited_once()
    assert len(audits) == 2
    assert audits[1]["details"]["shadow"] is True

    settings.RBAC_ENFORCE_TASK_ROUTES = True
    plan_handler.reset_mock()
    response = client.post("/api/mcp-stdio/plan/sessions/s/process")
    assert response.status_code == 403
    plan_handler.assert_not_awaited()
    assert len(audits) == 3

    # Same-org (org A) key whose user has no membership: shadow 200 + audit, enforce 403.
    selected["key"] = key_cls(
        key_id="key-a-outsider",
        scopes=frozenset({"task:write", "plan:write"}),
        user_id="outsider",
        org_id="A",
    )
    response = client.post("/api/mcp-stdio/tasks/p1:s/start")
    assert response.status_code == 403
    handler.assert_not_awaited()
    assert len(audits) == 4

    settings.RBAC_ENFORCE_TASK_ROUTES = False
    response = client.post("/api/mcp-stdio/tasks/p1:s/start")
    assert response.status_code == 200
    handler.assert_awaited_once()
    assert len(audits) == 5
    assert audits[4]["details"]["shadow"] is True

    selected["key"] = legacy
    for path in ("/api/mcp-stdio/tasks/p1:s/start", "/api/mcp-stdio/plan/sessions/s/process"):
        assert client.post(path).status_code == 200
    assert len(audits) == 5


@pytest.mark.parametrize(
    "path",
    [
        "/api/mcp-stdio/tasks/p1:s/logs",
        "/api/mcp-stdio/tasks/p1:s/worktree/diff",
    ],
)
def test_mcp_read_rejects_foreign_org_key_with_flag_off(mcp_env, path):
    client, settings, audits, _, _, _, _, read_handler, _ = mcp_env
    settings.RBAC_ENFORCE_TASK_ROUTES = False
    response = client.get(path)
    assert response.status_code == 403
    read_handler.assert_not_awaited()
    assert len(audits) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/api/mcp-stdio/tasks/p1:s",
        "/api/mcp-stdio/tasks/p1:s/status",
        "/api/mcp-stdio/tasks/p1:s/logs",
        "/api/mcp-stdio/tasks/p1:s/worktree/diff",
    ],
)
def test_mcp_read_same_org_and_legacy_keys(mcp_env, path):
    client, settings, audits, selected, legacy, _, _, read_handler, key_cls = mcp_env
    selected["key"] = key_cls(
        key_id="key-a-viewer",
        scopes=frozenset({"mcp:read"}),
        user_id="viewer-a",
        org_id="A",
    )
    response = client.get(path)
    assert response.status_code == 200
    assert audits == []

    selected["key"] = key_cls(
        key_id="key-a-outsider",
        scopes=frozenset({"mcp:read"}),
        user_id="outsider",
        org_id="A",
    )
    settings.RBAC_ENFORCE_TASK_ROUTES = False
    response = client.get(path)
    assert response.status_code == 200
    assert len(audits) == 1
    assert audits[0]["details"]["shadow"] is True

    settings.RBAC_ENFORCE_TASK_ROUTES = True
    read_handler.reset_mock()
    response = client.get(path)
    assert response.status_code == 403
    read_handler.assert_not_awaited()
    assert len(audits) == 2

    selected["key"] = legacy
    response = client.get(path)
    assert response.status_code == 200
    assert len(audits) == 2


def test_mcp_lists_filter_foreign_projects(mcp_env):
    client, _, _, selected, legacy, _, _, _, _ = mcp_env
    assert client.get("/api/mcp-stdio/projects").json() == [{"id": "pB"}]
    assert [t["project_id"] for t in client.get("/api/mcp-stdio/tasks").json()["tasks"]] == ["pB"]
    assert client.get("/api/mcp-stdio/tasks").json()["total"] == 1
    assert client.get("/api/mcp-stdio/tasks/running").json() == {"tasks": ["pB:sB"], "count": 1}

    selected["key"] = legacy
    assert [p["id"] for p in client.get("/api/mcp-stdio/projects").json()] == ["p1", "pB"]
    assert client.get("/api/mcp-stdio/tasks").json()["total"] == 2
    assert client.get("/api/mcp-stdio/tasks/running").json() == {
        "tasks": ["p1:s1", "pB:sB"],
        "count": 2,
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/mcp-stdio/plan/sessions",
        "/api/mcp-stdio/plan/sessions/s1",
        "/api/mcp-stdio/plan/sessions/s1/audit-pack",
    ],
)
def test_mcp_plan_read_requires_viewer(mcp_env, path):
    client, settings, audits, _, _, _, _, read_handler, _ = mcp_env
    settings.RBAC_ENFORCE_TASK_ROUTES = False
    assert client.get(path).status_code == 200
    assert len(audits) == 1
    assert audits[0]["details"]["shadow"] is True

    settings.RBAC_ENFORCE_TASK_ROUTES = True
    read_handler.reset_mock()
    assert client.get(path).status_code == 403
    read_handler.assert_not_awaited()
    assert len(audits) == 2


def test_console_attach_audit_carries_user_and_org(env, tmp_path, monkeypatch):
    client, _, _, bridge = env
    bridge.get_registry()._states["s1"] = bridge.SessionState(
        spec_id="s1",
        session_name="pfactory-task-s1",
        fifo_path=tmp_path / "s1.fifo",
        project_id="p1",
    )
    captured = {}

    async def _log(_db, **kw):
        captured.update(kw)

    monkeypatch.setattr(bridge, "log_audit_event", _log)
    response = client.post(
        "/api/tasks/p1:s1/agent-console/attach",
        json={"connection_id": "c1"},
        headers={"X-U": "member-a", "X-Org": "A"},
    )
    assert response.status_code == 200
    assert captured["user_id"] == "member-a"
    assert captured["org_id"] == "A"


@pytest.mark.parametrize("task_id", ["nope:s", "s"])
def test_mcp_org_key_on_unknown_project_denied_with_flag_off(mcp_env, task_id):
    # get_worktree_diff once scanned every project for a bare/unknown-prefix spec.
    client, settings, _, selected, _, _, _, read_handler, key_cls = mcp_env
    settings.RBAC_ENFORCE_TASK_ROUTES = False
    selected["key"] = key_cls(
        key_id="key-a", scopes=frozenset({"mcp:read"}), user_id="viewer-a", org_id="A"
    )
    response = client.get(f"/api/mcp-stdio/tasks/{task_id}/worktree/diff")
    assert response.status_code == 404
    read_handler.assert_not_awaited()


def test_mcp_project_registry_failure_fails_closed(mcp_env, monkeypatch):
    client, _, _, _, _, handler, _, read_handler, _ = mcp_env
    proxy = importlib.import_module("server.mcp_stdio.router")

    def _boom():
        raise RuntimeError("projects.json unreadable")

    monkeypatch.setattr(proxy.project_paths, "load_projects", _boom)
    assert client.get("/api/mcp-stdio/tasks/p1:s/worktree/diff").status_code == 503
    assert client.post("/api/mcp-stdio/tasks/p1:s/start").status_code == 503
    assert client.get("/api/mcp-stdio/projects").status_code == 503
    read_handler.assert_not_awaited()
    handler.assert_not_awaited()


def test_worktree_status_and_diff_need_exact_project(tmp_path, monkeypatch):
    """#958: a bare id or unknown prefix must not find p1's spec by scanning."""
    tasks = importlib.import_module("server.routes.tasks")
    proj = tmp_path / "p1"
    (proj / ".pfactory" / "specs" / "s1").mkdir(parents=True)
    (proj / ".pfactory" / "worktrees" / "tasks" / "s1").mkdir(parents=True)
    (tmp_path / "projects.json").write_text(json.dumps({"p1": {"path": str(proj)}}))
    monkeypatch.setattr(tasks, "get_data_dir", lambda: tmp_path)

    for handler in (tasks.get_worktree_status, tasks.get_worktree_diff):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(handler("s1"))
        assert exc.value.status_code == 400
    status = asyncio.run(tasks.get_worktree_status("nope:s1"))
    assert status == {"success": True, "data": {"exists": False}}
    diff = asyncio.run(tasks.get_worktree_diff("nope:s1"))
    assert diff == {"success": False, "error": "Project not found for task nope:s1"}


def test_merge_preview_needs_exact_project(tmp_path, monkeypatch):
    """#969: merge preview requires an exact project id."""
    tasks = importlib.import_module("server.routes.tasks")
    proj = tmp_path / "p1"
    (proj / ".pfactory" / "specs" / "s1").mkdir(parents=True)
    (tmp_path / "projects.json").write_text(json.dumps({"p1": {"path": str(proj)}}))
    monkeypatch.setattr(tasks, "get_data_dir", lambda: tmp_path)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(tasks.get_worktree_merge_preview("s1"))
    assert exc.value.status_code == 400
    assert asyncio.run(tasks.get_worktree_merge_preview("nope:s1")) == {
        "success": False,
        "error": "Task nope:s1 not found",
    }
    assert asyncio.run(tasks.get_worktree_merge_preview("p1:s1")) == {
        "success": False,
        "error": "No worktree found for this task",
    }
