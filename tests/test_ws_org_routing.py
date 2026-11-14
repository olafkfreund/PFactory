"""Org routing for the global events socket."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from server import auth
from server.database import engine
from server.routes import project_authz
from server.services import notification_service as notification_module
from server.services.notification_service import NotificationService
from server.services.task_log_writer import TaskLogWriter
from server.services.task_models import TaskPhase
from server.websockets import _dispatch, events, relay


@pytest.fixture
def socket_app(monkeypatch):
    app = FastAPI()
    app.include_router(events.router)
    principals = {
        "jwt-a": {"id": "a-only"},
        "jwt-owner": {"id": "owner"},
        "jwt-b": {"id": "other"},
        "acw_a": {"id": "owner", "org_id": "A", "api_key_id": "key"},
        "shared": None,
    }
    memberships = {"owner": [("A",), ("B",)], "a-only": [("A",)], "other": [("B",)]}
    setting = SimpleNamespace(RBAC_ENFORCE_TASK_ROUTES=True, DISABLE_AUTH=False)

    async def authenticate(ws):
        return principals[events._ws_extract_token(ws)]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def execute(self, stmt):
            uid = next(iter(stmt.compile().params.values()))
            return SimpleNamespace(all=lambda: memberships[uid])

    monkeypatch.setattr(engine, "async_session_factory", Session)
    monkeypatch.setattr(events, "authenticate_websocket", authenticate)
    monkeypatch.setattr(events, "get_settings", lambda: setting)
    monkeypatch.setattr(project_authz, "get_settings", lambda: setting)
    monkeypatch.setattr(relay, "publish", AsyncMock())
    monkeypatch.setattr(
        events, "load_projects", lambda: {"p-a": {"org_id": "A"}, "p-b": {"org_id": "B"}}
    )
    events._org_cache.clear()
    yield app, setting
    events._org_cache.clear()


def _connect(client, token):
    return client.websocket_connect("/ws/events", headers={"authorization": f"Bearer {token}"})


def _emit(client, event_type, payload):
    client.portal.call(events.broadcast_event, event_type, payload)


def test_project_events_reach_only_their_org(socket_app):
    app, _ = socket_app
    with TestClient(app) as client, _connect(client, "jwt-a") as a, _connect(client, "jwt-b") as b:
        _emit(client, "task:status", {"taskId": "p-b:001", "status": "done"})
        assert b.receive_json()["payload"]["status"] == "done"
        _emit(client, "task:status", {"taskId": "p-a:001", "status": "ready"})
        assert a.receive_json()["payload"]["status"] == "ready"


def test_service_and_bound_key(socket_app):
    app, _ = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "shared") as service,
    ):
        assert next(c for c in events._clients.values() if c.key_org_id).org_ids == {"A"}
        events.update_client_orgs("owner", {"A", "B"})
        _emit(client, "b", {"projectId": "p-b"})
        assert service.receive_json()["type"] == "b"
        _emit(client, "a", {"projectId": "p-a"})
        assert key.receive_json()["type"] == "a"


def test_real_task_status_emitter_routes_by_task_project(socket_app):
    app, _ = socket_app
    with TestClient(app) as client, _connect(client, "jwt-a") as a, _connect(client, "jwt-b") as b:
        client.portal.call(events.emit_task_status, "p-b:001-x", "running")
        received = b.receive_json()
        assert received["payload"]["taskId"] == "p-b:001-x"
        assert received["payload"]["status"] == "running"
        _emit(client, "sentinel", {"projectId": "p-a"})
        assert a.receive_json()["type"] == "sentinel"


@pytest.mark.parametrize("payload", [{}, {"taskId": "001-bare"}, {"projectId": "missing"}])
def test_unresolved_events_are_service_only(socket_app, payload):
    app, _ = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "jwt-a") as jwt,
        _connect(client, "shared") as service,
    ):
        _emit(client, "unresolved", payload)
        assert service.receive_json()["type"] == "unresolved"
        _emit(client, "known", {"projectId": "p-a"})
        assert key.receive_json()["type"] == "known"
        assert jwt.receive_json()["type"] == "known"


def test_registry_failure_is_service_only(socket_app, monkeypatch):
    app, _ = socket_app

    def fail():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(events, "load_projects", fail)
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "shared") as service,
    ):
        _emit(client, "failed", {"projectId": "p-a"})
        assert service.receive_json()["type"] == "failed"
        client.portal.call(events.send_to_org, "A", "sentinel", {})
        assert key.receive_json()["type"] == "sentinel"


def test_user_events_require_matching_org_for_key(socket_app):
    app, _ = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "jwt-owner") as jwt,
    ):
        client.portal.call(events.send_to_user, "owner", "cli-account-auth", {"success": True})
        assert jwt.receive_json()["type"] == "cli-account-auth"
        client.portal.call(
            events.send_to_user, "owner", "notification:new", {"data": {"org_id": "B"}}
        )
        assert jwt.receive_json()["payload"]["data"]["org_id"] == "B"
        client.portal.call(
            events.send_to_user, "owner", "notification:new", {"data": {"org_id": "A"}}
        )
        assert key.receive_json()["payload"]["data"]["org_id"] == "A"
        # Membership gone (failed lookup or removal): the key socket gets nothing.
        events.update_client_orgs("owner", set())
        client.portal.call(events.send_to_user, "owner", "dropped", {"org_id": "A"})
        events.update_client_orgs("owner", {"A"})
        client.portal.call(events.send_to_user, "owner", "sentinel", {"org_id": "A"})
        assert key.receive_json()["type"] == "sentinel"


def test_notify_org_overrides_untrusted_data(socket_app, monkeypatch):
    app, _ = socket_app
    service = NotificationService()
    monkeypatch.setattr(service, "_maybe_send_email", AsyncMock())
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "jwt-owner") as jwt,
    ):
        # The notification module captured its own session factory at import time.
        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def execute(self, _stmt):
                return SimpleNamespace(all=lambda: [("owner",)])

        monkeypatch.setattr(notification_module, "async_session_factory", Session)

        async def send():
            return await service.notify_org(
                org_id="A", type="test", title="T", message="M", data={"org_id": "B"}
            )

        client.portal.call(send)
        assert key.receive_json()["payload"]["data"]["org_id"] == "A"
        assert jwt.receive_json()["payload"]["data"]["org_id"] == "A"


def test_relay_old_broadcast_and_empty_org(socket_app):
    app, _ = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "jwt-a") as jwt,
        _connect(client, "shared") as service,
    ):
        client.portal.call(_dispatch.dispatch, "ws:broadcast", {"event_type": "old", "payload": {}})
        assert service.receive_json()["type"] == "old"
        client.portal.call(
            _dispatch.dispatch, "ws:org", {"org_id": "", "event_type": "empty", "payload": {}}
        )
        assert service.receive_json()["type"] == "empty"
        client.portal.call(events.send_to_org, "A", "sentinel", {})
        assert key.receive_json()["type"] == "sentinel"
        assert jwt.receive_json()["type"] == "sentinel"


def test_flag_tracks_rest_dependency_on_open_socket(socket_app, monkeypatch):
    app, setting = socket_app
    monkeypatch.setattr(
        project_authz.project_paths, "load_projects", lambda: {"p-b": {"org_id": "B"}}
    )
    monkeypatch.setattr(project_authz, "_audit_denial", AsyncMock())
    db = SimpleNamespace(
        rollback=AsyncMock(),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
    )
    request = Request({"type": "http"})
    request.state.user = {"id": "a-only"}
    checker = project_authz.ProjectAccessChecker()
    with TestClient(app) as client, _connect(client, "jwt-a") as ws:
        setting.RBAC_ENFORCE_TASK_ROUTES = False
        # An explicit org send stays members-only even in shadow mode.
        client.portal.call(events.send_to_org, "B", "explicit", {})
        _emit(client, "shadow", {"projectId": "p-b"})
        assert ws.receive_json()["type"] == "shadow"
        client.portal.call(checker.__call__, "p-b", request, db)
        setting.RBAC_ENFORCE_TASK_ROUTES = True
        _emit(client, "blocked", {"projectId": "p-b"})
        _emit(client, "allowed", {"projectId": "p-a"})
        assert ws.receive_json()["type"] == "allowed"
        with pytest.raises(HTTPException) as exc:
            client.portal.call(checker.__call__, "p-b", request, db)
        assert exc.value.status_code == 403


def test_event_org_cache_expiry_and_reassignment(monkeypatch):
    events._org_cache.clear()
    clock = [0.0]
    projects = {"p": {}}
    calls = []

    def load():
        calls.append(1)
        return projects

    monkeypatch.setattr(events.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(events, "load_projects", load)
    assert events._event_org({"projectId": "p"}) is None
    projects["p"]["org_id"] = "A"
    assert events._event_org({"projectId": "p"}) is None
    assert len(calls) == 1
    clock[0] = 31.0
    assert events._event_org({"projectId": "p"}) == "A"
    projects["p"]["org_id"] = "B"
    clock[0] = 62.0
    assert events._event_org({"projectId": "p"}) == "B"


@pytest.mark.parametrize("org", ["A", None])
@pytest.mark.parametrize("failure", [False, True])
def test_key_revocation_closes_socket(socket_app, monkeypatch, failure, org):
    app, _ = socket_app
    monkeypatch.setattr(events, "_KEY_RECHECK_SECONDS", 0)
    principal = {"id": "owner", "api_key_id": "key"} | ({"org_id": org} if org else {})
    if failure:
        monkeypatch.setattr(
            auth,
            "_try_authenticate_personal_key",
            AsyncMock(side_effect=[principal, RuntimeError("validator unavailable")]),
        )
    else:
        monkeypatch.setattr(events, "_ws_acw_principal", AsyncMock(side_effect=[principal, None]))

    async def authenticate(ws):
        return await events._ws_acw_principal(events._ws_extract_token(ws))

    monkeypatch.setattr(events, "authenticate_websocket", authenticate)
    with TestClient(app) as client, _connect(client, "acw_a") as ws:
        with pytest.raises(Exception) as exc:
            ws.receive_text()
        assert getattr(exc.value, "code", None) == 4001


def test_task_log_writer_stream_carries_project_id(socket_app, tmp_path):
    app, _ = socket_app
    writer = TaskLogWriter(tmp_path, project_id="p-a")

    async def add_and_flush():
        writer.add_entry("001-x", TaskPhase.CODING, "text", "hi")
        await asyncio.sleep(0.2)

    with TestClient(app) as client, _connect(client, "jwt-a") as a, _connect(client, "jwt-b") as b:
        client.portal.call(add_and_flush)
        received = a.receive_json()
        assert received["type"] == "task-logs:stream"
        assert received["payload"]["projectId"] == "p-a"
        _emit(client, "sentinel", {"projectId": "p-b"})
        assert b.receive_json()["type"] == "sentinel"


@pytest.mark.parametrize("event", ["cli-account-auth", "github:auth-complete"])
def test_org_less_login_events_follow_the_starting_principal(socket_app, event):
    app, _ = socket_app
    with (
        TestClient(app) as client,
        _connect(client, "jwt-owner") as owner,
        _connect(client, "jwt-b") as other,
        _connect(client, "acw_a") as key,
        _connect(client, "shared") as service,
    ):
        client.portal.call(events.emit_org_less, {"id": "owner"}, event, {"ok": True})
        assert owner.receive_json()["type"] == event
        client.portal.call(
            events.emit_org_less, {"id": "default", "is_service": True}, event, {"n": 2}
        )
        assert service.receive_json()["payload"] == {"n": 2}
        client.portal.call(events.emit_org_less, None, event, {"n": 3})
        assert service.receive_json()["payload"] == {"n": 3}
        # Sentinels prove nothing else was queued ahead of them.
        client.portal.call(events.send_to_org, "B", "sentinel", {})
        assert other.receive_json()["type"] == "sentinel"
        client.portal.call(events.send_to_org, "A", "sentinel", {})
        assert key.receive_json()["type"] == "sentinel"
        assert owner.receive_json()["type"] == "sentinel"
