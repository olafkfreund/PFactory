"""Terminal sessions are bound to their owner and project (Factory#3740)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "apps" / "web-server"))
sys.path.insert(0, str(_ROOT / "apps" / "backend"))

from server.services import git_utils, project_paths  # noqa: E402
from server.pty import access  # noqa: E402
from server.pty.manager import PTYManager  # noqa: E402
from server.pty.session import PTYSession  # noqa: E402
from server.routes import terminal  # noqa: E402
from server.websockets import terminal as terminal_ws  # noqa: E402

ALICE = {"id": "alice", "role": "user"}
BOB = {"id": "bob", "role": "user"}
ADMIN_NOT_SERVICE = {"id": "root", "role": "admin"}
SERVICE = {"id": "default", "is_service": True}
USERS = {"alice": ALICE, "bob": BOB, "root": ADMIN_NOT_SERVICE, "svc": SERVICE}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    projects = {
        "a": {"path": str(tmp_path / "a"), "org_id": "A"},
        "b": {"path": str(tmp_path / "b"), "org_id": "B"},
    }
    state = {"members": {"alice": {"a"}, "bob": {"b"}}, "error": False}
    monkeypatch.setenv("PROJECT_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(project_paths, "load_projects", lambda: projects)
    monkeypatch.setattr(git_utils, "load_projects", lambda: projects)
    monkeypatch.setattr(terminal, "load_projects", lambda: projects)

    class Session:
        async def __aenter__(self):
            if state["error"]:
                raise RuntimeError("database unavailable")
            return self

        async def __aexit__(self, *_):
            pass

    async def authorize(user, project_id, _db):
        key_org = user.get("api_key_id") and user.get("org_id") != "A"
        if project_id not in state["members"].get(user["id"], set()) or key_org:
            raise PermissionError("not a member")

    monkeypatch.setattr(access.engine, "async_session_factory", Session)
    monkeypatch.setattr(access, "authorize_project_for_user", authorize)

    monkeypatch.setattr(PTYManager, "_resolve_claude_token", lambda self: (None, None, None))
    monkeypatch.setattr(PTYSession, "start", lambda self: None)
    monkeypatch.setattr(PTYSession, "is_alive", lambda self: True)
    monkeypatch.setattr(PTYSession, "close", lambda self: None)
    manager = PTYManager()
    monkeypatch.setattr(terminal, "get_pty_manager", lambda: manager)
    monkeypatch.setattr(terminal_ws, "get_pty_manager", lambda: manager)

    app = FastAPI()
    app.include_router(terminal.router, prefix="/api/terminals")
    app.include_router(terminal_ws.router)

    @app.middleware("http")
    async def principal(request, call_next):
        request.state.user = USERS.get(request.headers.get("x-user"))
        return await call_next(request)

    return SimpleNamespace(
        client=TestClient(app),
        manager=manager,
        state=state,
        root=tmp_path,
        monkeypatch=monkeypatch,
    )


def _create(env, who, **body):
    return env.client.post("/api/terminals", json=body, headers={"x-user": who})


def _as(env, who, method, path, **kw):
    return env.client.request(method, path, headers={"x-user": who}, **kw)


def test_owner_uses_own_terminal_and_the_other_user_gets_404(env):
    created = _create(env, "alice", id="t1", cwd=str(env.root / "a"))
    assert created.status_code == 201, created.text
    session = env.manager.get_session("t1")
    assert (session.owner_id, session.project_id) == ("alice", "a")

    for method, path in [
        ("get", "/api/terminals/t1"),
        ("post", "/api/terminals/t1/resize"),
    ]:
        body = {"json": {"cols": 80, "rows": 24}} if "resize" in path else {}
        assert _as(env, "alice", method, path, **body).status_code == 200
        assert _as(env, "bob", method, path, **body).status_code == 404
    assert _as(env, "bob", "get", "/api/terminals/t1/alive").json()["data"] is False
    assert _as(env, "alice", "get", "/api/terminals/t1/alive").json()["data"] is True
    assert _as(env, "bob", "delete", "/api/terminals/t1").status_code == 404
    assert _as(env, "bob", "post", "/api/terminals/t1/buffer", json={}).status_code == 404

    assert _as(env, "alice", "get", "/api/terminals").json()["count"] == 1
    assert _as(env, "bob", "get", "/api/terminals").json()["count"] == 0
    assert _as(env, "bob", "get", "/api/terminals/sessions").json()["data"] == []
    assert _as(env, "svc", "get", "/api/terminals").json()["count"] == 1
    assert _as(env, "svc", "delete", "/api/terminals/t1").status_code == 204
    assert env.manager.get_session("t1") is None


def test_create_requires_an_authorized_project_that_contains_the_directory(env):
    assert _create(env, "alice", cwd=str(env.root / "b")).status_code == 403
    assert _create(env, "alice", projectId="b").status_code == 403
    # project A's id cannot authorize project B's directory
    assert _create(env, "alice", projectId="a", cwd=str(env.root / "b")).status_code == 403
    # projectless (home directory) terminals stay service-only
    assert _create(env, "alice", cwd=str(env.root)).status_code == 403
    assert _create(env, "root", cwd=str(env.root / "a")).status_code == 403
    assert _create(env, "alice", projectId="a").status_code == 201
    assert _create(env, "alice", projectPath=str(env.root / "a")).status_code == 201
    assert _create(env, "svc", cwd=str(env.root)).status_code == 201
    env.state["error"] = True
    assert _create(env, "alice", projectId="a").status_code == 403


def test_duplicate_id_is_rejected_without_spawning(env, monkeypatch):
    assert _create(env, "alice", id="t1", projectId="a").status_code == 201
    started = []
    monkeypatch.setattr(PTYSession, "start", lambda self: started.append(self))
    assert _create(env, "bob", id="t1", projectId="b").status_code == 409
    assert started == []
    assert env.manager.get_session("t1").owner_id == "alice"


def test_close_only_closes_the_authorized_session(env):
    first = env.manager.create_session(session_id="t1", owner_id="alice")
    assert env.manager.close_session("t1")
    second = env.manager.create_session(session_id="t1", owner_id="bob")
    assert not env.manager.close_session("t1", expected=first)
    assert env.manager.get_session("t1") is second


def test_sessions_filter_by_project_path_and_id(env):
    _create(env, "alice", id="t1", projectId="a")
    _create(env, "svc", id="t2", cwd=str(env.root / "b"))
    path = str(env.root / "a")
    assert [
        s["id"]
        for s in _as(env, "svc", "get", "/api/terminals/sessions", params={"project": path}).json()[
            "data"
        ]
    ] == ["t1"]
    assert [
        s["id"]
        for s in _as(env, "svc", "get", "/api/terminals/sessions", params={"project": "b"}).json()[
            "data"
        ]
    ] == ["t2"]
    assert (
        _as(env, "svc", "get", "/api/terminals/sessions", params={"project": "/nowhere"}).json()[
            "data"
        ]
        == []
    )


def test_buffer_lands_in_the_sessions_project(env):
    _create(env, "alice", id="t1", projectId="a")
    res = _as(
        env, "alice", "post", "/api/terminals/t1/buffer", json={"buffer": "x", "projectId": "b"}
    )
    assert res.status_code == 200, res.text
    assert str(env.root / "a") in res.json()["sessionFile"]


def test_worktree_clear_and_cleanup_routes(env):
    for method, path, kw in [
        ("get", "/api/terminals/worktrees", {"params": {"project": str(env.root / "b")}}),
        ("delete", "/api/terminals/worktrees/x", {"params": {"project": str(env.root / "b")}}),
        (
            "post",
            "/api/terminals/worktrees",
            {"json": {"terminalId": "t", "name": "x", "projectPath": str(env.root / "b")}},
        ),
        ("delete", "/api/terminals/sessions", {"params": {"project": str(env.root / "b")}}),
        ("delete", "/api/terminals/sessions", {}),
        ("post", "/api/terminals/cleanup", {}),
    ]:
        assert _as(env, "alice", method, path, **kw).status_code == 403, (method, path)
    assert _as(env, "svc", "post", "/api/terminals/cleanup").status_code == 200
    assert (
        _as(
            env,
            "alice",
            "delete",
            "/api/terminals/sessions",
            params={"project": str(env.root / "a")},
        ).status_code
        == 200
    )


@pytest.mark.asyncio
async def test_may_use_rule(env):
    session = PTYSession(owner_id="alice", project_id="a")
    assert await access.may_use(SERVICE, session)
    assert await access.may_use(ALICE, session)
    assert not await access.may_use(BOB, session)
    assert not await access.may_use(None, session)
    assert not await access.may_use({"role": "admin"}, session)
    assert not await access.may_use(ADMIN_NOT_SERVICE, session)
    assert not await access.may_use({"id": "alice", "api_key_id": "k", "org_id": "B"}, session)
    assert await access.may_use({"id": "alice", "api_key_id": "k", "org_id": "A"}, session)
    env.state["error"] = True
    assert not await access.may_use(ALICE, session)
    env.state["error"] = False
    env.state["members"]["alice"] = set()
    assert not await access.may_use(ALICE, session)


@pytest.fixture
def sockets(env, monkeypatch):
    principal = {"user": ALICE, "token": "jwt"}

    async def authenticate(_ws):
        return principal["user"]

    async def pending(*_a):
        await asyncio.Event().wait()

    monkeypatch.setattr(terminal_ws, "authenticate_websocket", authenticate)
    monkeypatch.setattr(terminal_ws, "_ws_extract_token", lambda _ws: principal["token"])
    monkeypatch.setattr(terminal_ws, "create_pty_reader", pending)
    monkeypatch.setattr(terminal_ws, "_RECHECK_SECONDS", 0.05)
    env.manager.create_session(session_id="t1", owner_id="alice", project_id="a")
    env.principal = principal
    return env


def _first(env):
    with env.client.websocket_connect("/ws/terminal/t1") as ws:
        return ws.receive_json()


def test_ws_owner_connects_others_get_4004(sockets):
    assert _first(sockets)["type"] == "connected"
    for user in (BOB, {"id": "alice", "api_key_id": "k", "org_id": "B"}):
        sockets.principal["user"] = user
        with sockets.client.websocket_connect("/ws/terminal/t1") as ws:
            assert ws.receive_json()["type"] == "error"
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4004


def test_ws_legacy_and_service_connect(sockets):
    for user in (None, SERVICE):
        sockets.principal["user"] = user
        assert _first(sockets)["type"] == "connected"


def test_ws_membership_loss_closes_4003(sockets):
    with sockets.client.websocket_connect("/ws/terminal/t1") as ws:
        assert ws.receive_json()["type"] == "connected"
        sockets.state["members"]["alice"] = set()
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4003


@pytest.mark.parametrize("outcome", ["revoked", "raises"])
def test_ws_revoked_key_closes_4001(sockets, monkeypatch, outcome):
    sockets.principal["user"] = {"id": "alice", "api_key_id": "k", "org_id": "A"}
    sockets.principal["token"] = "acw_secret"

    async def validate(_token):
        if outcome == "raises":
            raise RuntimeError("down")

    monkeypatch.setattr(terminal_ws, "_ws_acw_principal", validate)
    with sockets.client.websocket_connect("/ws/terminal/t1") as ws:
        assert ws.receive_json()["type"] == "connected"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4001
