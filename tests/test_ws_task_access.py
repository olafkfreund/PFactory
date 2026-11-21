"""Org access and key re-validation on per-task WebSockets."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

_ROOT = Path(__file__).resolve().parents[1]
for _rel in (("apps", "web-server"), ("apps", "backend")):
    _path = str(_ROOT.joinpath(*_rel))
    if _path not in sys.path:
        sys.path.insert(0, _path)

from server import auth  # noqa: E402
from server.routes import project_authz  # noqa: E402
from server.websockets import logs, progress, task_access  # noqa: E402

ENDPOINTS = ("logs", "progress")


def _engine() -> Any:
    return importlib.import_module("server.database.engine")


@pytest.fixture
def task_ws_app(monkeypatch):
    app = FastAPI()
    app.include_router(logs.router)
    app.include_router(progress.router)

    principals = {
        "jwt-a": {"id": "user-a", "role": "user"},
        "jwt-b": {"id": "user-b", "role": "user"},
        "acw_a": {
            "id": "owner",
            "org_id": "A",
            "api_key_id": "key-a",
            "scopes": ("mcp:read",),
            "role": "user",
        },
        "acw_b": {
            "id": "owner",
            "org_id": "B",
            "api_key_id": "key-b",
            "scopes": ("mcp:read",),
            "role": "user",
        },
        "shared": {"id": "default", "is_service": True},
    }
    memberships: dict[tuple[str, str], str] = {
        ("A", "user-a"): "viewer",
        ("A", "owner"): "member",
        ("B", "user-b"): "viewer",
        ("B", "owner"): "member",
    }
    projects = {
        "p-a": {"org_id": "A"},
        "p-b": {"org_id": "B"},
    }
    setting = SimpleNamespace(RBAC_ENFORCE_TASK_ROUTES=True, DISABLE_AUTH=False)

    async def authenticate(ws):
        token = task_access._ws_extract_token(ws)
        if token not in principals:
            await ws.close(code=4001, reason="Unauthorized")
            raise auth.WebSocketAuthError("Invalid token")
        return principals[token]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def rollback(self):
            return None

        async def execute(self, stmt):
            params = list(stmt.compile().params.values())
            org_id, user_id = params[0], params[1]
            role = memberships.get((org_id, user_id))
            member = SimpleNamespace(org_id=org_id, user_id=user_id, role=role) if role else None
            return SimpleNamespace(scalar_one_or_none=lambda: member)

    monkeypatch.setattr(_engine(), "async_session_factory", Session)
    monkeypatch.setattr(task_access, "authenticate_websocket", authenticate)
    monkeypatch.setattr(
        task_access,
        "_ws_acw_principal",
        AsyncMock(side_effect=principals.get),
    )
    monkeypatch.setattr(task_access, "get_settings", lambda: setting)
    monkeypatch.setattr(project_authz, "get_settings", lambda: setting)
    monkeypatch.setattr(project_authz, "_audit_denial", AsyncMock())
    monkeypatch.setattr(project_authz.project_paths, "load_projects", lambda: projects)
    yield app, setting, memberships, principals


def _connect(client: TestClient, kind: str, task_id: str, token: str) -> Any:
    return client.websocket_connect(
        f"/ws/tasks/{task_id}/{kind}",
        headers={"authorization": f"Bearer {token}"},
    )


def _assert_close_code(client: TestClient, kind: str, task_id: str, token: str, code: int) -> None:
    with pytest.raises(WebSocketDisconnect) as exc, _connect(client, kind, task_id, token) as ws:
        ws.receive_json()
    assert exc.value.code == code


@pytest.mark.parametrize("kind", ENDPOINTS)
@pytest.mark.parametrize("token", ["jwt-a", "acw_a", "shared"])
def test_member_and_service_allowed(task_ws_app, kind, token):
    app, *_ = task_ws_app
    with TestClient(app) as client, _connect(client, kind, "p-a:001", token) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert msg["task_id"] == "p-a:001"


@pytest.mark.parametrize("kind", ENDPOINTS)
@pytest.mark.parametrize("task_id", ["p-a", "001-bare", "p-a:", ":001"])
@pytest.mark.parametrize("token", ["jwt-a", "acw_a"])
def test_bare_task_id_denied_4404_even_when_equal_to_accessible_project(
    task_ws_app, kind, task_id, token
):
    app, *_ = task_ws_app
    with TestClient(app) as client:
        _assert_close_code(client, kind, task_id, token, 4404)


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_non_member_other_org_key_and_unknown_project_close_4404(task_ws_app, kind):
    app, *_ = task_ws_app
    with TestClient(app) as client:
        _assert_close_code(client, kind, "p-b:001", "jwt-a", 4404)
        _assert_close_code(client, kind, "p-a:001", "acw_b", 4404)
        _assert_close_code(client, kind, "missing:001", "jwt-a", 4404)
        _assert_close_code(client, kind, "missing:001", "acw_a", 4404)


def _install_failure(monkeypatch, failure: str) -> None:
    if failure == "registry":
        monkeypatch.setattr(
            project_authz.project_paths,
            "load_projects",
            lambda: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
        )
    elif failure == "session":
        monkeypatch.setattr(
            _engine(),
            "async_session_factory",
            lambda: (_ for _ in ()).throw(RuntimeError("session unavailable")),
        )
    else:

        class FailingSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, _stmt):
                raise RuntimeError("query unavailable")

        monkeypatch.setattr(_engine(), "async_session_factory", FailingSession)


@pytest.mark.parametrize("kind", ENDPOINTS)
@pytest.mark.parametrize("failure", ["registry", "session", "query"])
@pytest.mark.parametrize("token", ["jwt-a", "acw_a"])
def test_lookup_exception_at_connect_closes_4404_when_strict(
    task_ws_app, monkeypatch, kind, failure, token
):
    app, *_ = task_ws_app
    _install_failure(monkeypatch, failure)
    with TestClient(app) as client:
        _assert_close_code(client, kind, "p-a:001", token, 4404)


@pytest.mark.parametrize("kind", ENDPOINTS)
@pytest.mark.parametrize("failure", ["registry", "session", "query"])
def test_lookup_exception_at_recheck_closes_4001(task_ws_app, monkeypatch, kind, failure):
    app, _, _, principals = task_ws_app
    monkeypatch.setattr(task_access, "_KEY_RECHECK_SECONDS", 0)

    async def recheck_principal(tok):
        _install_failure(monkeypatch, failure)
        return principals.get(tok)

    monkeypatch.setattr(task_access, "_ws_acw_principal", recheck_principal)
    with TestClient(app) as client, _connect(client, kind, "p-a:001", "acw_a") as ws:
        assert ws.receive_json()["type"] == "connected"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4001


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_key_owner_removed_after_interval_closes_4001(task_ws_app, monkeypatch, kind):
    app, _, memberships, principals = task_ws_app
    monkeypatch.setattr(task_access, "_KEY_RECHECK_SECONDS", 0)

    async def recheck_principal(tok):
        memberships.pop(("A", "owner"), None)
        return principals.get(tok)

    monkeypatch.setattr(task_access, "_ws_acw_principal", recheck_principal)
    with TestClient(app) as client, _connect(client, kind, "p-a:001", "acw_a") as ws:
        assert ws.receive_json()["type"] == "connected"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4001


@pytest.mark.parametrize("kind", ENDPOINTS)
@pytest.mark.parametrize("raises", [False, True])
def test_key_recheck_validator_none_or_raising_closes_4001(task_ws_app, monkeypatch, kind, raises):
    app, *_ = task_ws_app
    monkeypatch.setattr(task_access, "_KEY_RECHECK_SECONDS", 0)
    if raises:
        monkeypatch.setattr(
            auth,
            "_try_authenticate_personal_key",
            AsyncMock(side_effect=RuntimeError("validator error")),
        )
        monkeypatch.setattr(task_access, "_ws_acw_principal", auth._ws_acw_principal)
    else:
        monkeypatch.setattr(
            task_access,
            "_ws_acw_principal",
            AsyncMock(return_value=None),
        )
    with TestClient(app) as client, _connect(client, kind, "p-a:001", "acw_a") as ws:
        assert ws.receive_json()["type"] == "connected"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4001


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_shadow_flag_off_and_on_semantics(task_ws_app, monkeypatch, kind):
    app, setting, *_ = task_ws_app
    with TestClient(app) as client:
        for enforced in (False, True):
            setting.RBAC_ENFORCE_TASK_ROUTES = enforced
            _assert_close_code(client, kind, "p-b:001", "jwt-a", 4404)
            _assert_close_code(client, kind, "p-a:001", "acw_b", 4404)

        _install_failure(monkeypatch, "registry")
        _assert_close_code(client, kind, "p-a:001", "jwt-a", 4404)
        _assert_close_code(client, kind, "p-a:001", "acw_a", 4404)
