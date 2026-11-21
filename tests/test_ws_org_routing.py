"""Org routing for the global events socket."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from server import auth
from server.routes import project_authz
from server.services import notification_service as notification_module
from server.services.notification_service import NotificationService
from server.services.task_log_writer import TaskLogWriter
from server.services.task_models import TaskPhase
from server.websockets import _dispatch, events, relay


def _engine() -> Any:
    # Resolved at run time: other tests swap server.database.engine in sys.modules,
    # so an import-time handle goes stale (as in tests/test_ws_acw_auth.py).
    return importlib.import_module("server.database.engine")


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

    monkeypatch.setattr(_engine(), "async_session_factory", Session)
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


def _connect(client: Any, token: str) -> Any:
    return client.websocket_connect("/ws/events", headers={"authorization": f"Bearer {token}"})


def _emit(client: Any, event_type: str, payload: Any) -> None:
    client.portal.call(events.broadcast_event, event_type, payload)


def test_project_events_reach_only_their_org(socket_app):
    app, _ = socket_app
    with TestClient(app) as client, _connect(client, "jwt-a") as a, _connect(client, "jwt-b") as b:
        _emit(client, "task:status", {"taskId": "p-b:001", "status": "done"})
        assert b.receive_json()["payload"]["status"] == "done"
        _emit(client, "task:status", {"taskId": "p-a:001", "status": "ready"})
        assert a.receive_json()["payload"]["status"] == "ready"
        # A sentinel proves A's event was not queued for B ahead of it.
        client.portal.call(events.send_to_org, "B", "sentinel", {})
        assert b.receive_json()["type"] == "sentinel"


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
        with pytest.raises(HTTPException) as exc:
            client.portal.call(checker.__call__, "p-b", request, db)
        assert exc.value.status_code == 404
        setting.RBAC_ENFORCE_TASK_ROUTES = True
        _emit(client, "blocked", {"projectId": "p-b"})
        _emit(client, "allowed", {"projectId": "p-a"})
        assert ws.receive_json()["type"] == "allowed"
        with pytest.raises(HTTPException) as exc:
            client.portal.call(checker.__call__, "p-b", request, db)
        assert exc.value.status_code == 404


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


def _no_database() -> Any:
    raise RuntimeError("membership lookup unavailable")


def _wait_for_no_orgs(client: Any, socket: Any) -> None:
    for _ in range(200):
        if not socket.org_ids:
            return
        client.portal.call(asyncio.sleep, 0.01)
    raise AssertionError("memberships were not reloaded")


def test_key_socket_ignores_shadow_mode_and_follows_membership(socket_app, monkeypatch):
    app, setting = socket_app[:2]
    monkeypatch.setattr(events, "_KEY_RECHECK_SECONDS", 0)
    monkeypatch.setattr(
        events, "_ws_acw_principal", AsyncMock(return_value={"id": "owner", "org_id": "A"})
    )
    setting.RBAC_ENFORCE_TASK_ROUTES = False
    with TestClient(app) as client, _connect(client, "acw_a") as key:
        # Shadow mode never widens a key socket: B's event is skipped.
        _emit(client, "other-org", {"projectId": "p-b"})
        _emit(client, "unresolved", {"specId": "001-x"})
        _emit(client, "own-org", {"projectId": "p-a"})
        assert key.receive_json()["type"] == "own-org"
        # The membership lookup now fails: the next re-check empties the socket's orgs.
        monkeypatch.setattr(_engine(), "async_session_factory", _no_database)
        key.send_text("wake the receive loop")
        socket = next(iter(events._clients.values()))
        _wait_for_no_orgs(client, socket)
        assert not events._may_receive(socket, "A")


def test_broadcast_relays_shadow_flag_and_dispatch_honours_it(socket_app):
    app, setting = socket_app
    with TestClient(app) as client, _connect(client, "jwt-a") as ws:
        _emit(client, "event", {"projectId": "p-b"})
        client.portal.call(events.send_to_org, "B", "explicit", {})
        former_broadcast, explicit = (call.args for call in relay.publish.await_args_list)
        assert former_broadcast[0] == "ws:org"
        assert former_broadcast[1]["org_id"] == "B"
        assert former_broadcast[1]["shadow"] is True
        assert "shadow" not in explicit[1]

        setting.RBAC_ENFORCE_TASK_ROUTES = False
        strict = {"org_id": "B", "event_type": "strict", "payload": {}}
        client.portal.call(_dispatch.dispatch, "ws:org", strict)
        lenient = {"org_id": "B", "event_type": "lenient", "payload": {}, "shadow": True}
        client.portal.call(_dispatch.dispatch, "ws:org", lenient)
        assert ws.receive_json()["type"] == "lenient"


def test_legacy_broadcast_reaches_jwt_sockets_only_in_shadow_mode(socket_app):
    app, setting = socket_app
    setting.RBAC_ENFORCE_TASK_ROUTES = False
    with (
        TestClient(app) as client,
        _connect(client, "acw_a") as key,
        _connect(client, "jwt-a") as jwt,
    ):
        client.portal.call(_dispatch.dispatch, "ws:broadcast", {"event_type": "old", "payload": {}})
        assert jwt.receive_json()["type"] == "old"
        setting.RBAC_ENFORCE_TASK_ROUTES = True
        client.portal.call(_dispatch.dispatch, "ws:broadcast", {"event_type": "old", "payload": {}})
        # Neither the strict JWT socket nor the key socket (in either mode) got one.
        client.portal.call(events.send_to_org, "A", "sentinel", {})
        assert jwt.receive_json()["type"] == "sentinel"
        assert key.receive_json()["type"] == "sentinel"


def test_login_emitters_route_by_starting_principal(socket_app, monkeypatch):
    from server.routes import cli_accounts, github

    app, _ = socket_app
    monkeypatch.setattr(github, "_gh_auth_status", {})
    monkeypatch.setattr(github, "_gh_auth_proc", None)

    async def run_cli(principal):
        await asyncio.to_thread(
            cli_accounts._broadcast_cli_auth_event,
            "codex",
            True,
            asyncio.get_running_loop(),
            principal,
        )

    class FailedProc:
        returncode = 1

        async def wait(self):
            return 1

    service_principal = {"id": "default", "is_service": True}
    with (
        TestClient(app) as client,
        _connect(client, "jwt-owner") as owner,
        _connect(client, "jwt-b") as other,
        _connect(client, "acw_a") as key,
        _connect(client, "shared") as service,
    ):
        client.portal.call(run_cli, {"id": "owner"})
        assert owner.receive_json()["type"] == "cli-account-auth"
        client.portal.call(github._monitor_gh_auth, FailedProc(), {"id": "owner"})
        assert owner.receive_json()["type"] == "github:auth-complete"
        client.portal.call(run_cli, service_principal)
        assert service.receive_json()["type"] == "cli-account-auth"
        client.portal.call(github._monitor_gh_auth, FailedProc(), service_principal)
        assert service.receive_json()["type"] == "github:auth-complete"
        # Sentinels prove nothing else was queued ahead of them.
        client.portal.call(events.send_to_org, "B", "sentinel", {})
        assert other.receive_json()["type"] == "sentinel"
        client.portal.call(events.send_to_org, "A", "sentinel", {})
        assert key.receive_json()["type"] == "sentinel"
        assert owner.receive_json()["type"] == "sentinel"
        client.portal.call(events.send_to_org, events.SERVICE_ONLY_ORG, "sentinel", {})
        assert service.receive_json()["type"] == "sentinel"


def test_org_bound_key_without_owner_is_not_a_service_client():
    client = events.ConnectedClient(websocket=None, user_id=None, key_org_id="A")  # type: ignore[arg-type]
    assert not events._may_receive(client, "B")
    assert not events._may_receive(client, None, shadow=True)


def test_payload_org_falls_back_when_top_level_org_is_empty():
    assert events._payload_org({"org_id": "", "data": {"org_id": "A"}}) == "A"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"type": "access"}, {"sub": "", "type": "access"}])
async def test_jwt_without_sub_closes_4001(monkeypatch, payload):
    shared = "shared"
    monkeypatch.setattr(
        auth, "get_settings", lambda: SimpleNamespace(DISABLE_AUTH=False, API_TOKEN=shared)
    )
    monkeypatch.setattr(auth, "_try_decode_jwt", lambda _raw: payload)
    ws = SimpleNamespace(
        headers={"authorization": "Bearer no-sub"},
        query_params={},
        close=AsyncMock(),
    )
    with pytest.raises(auth.WebSocketAuthError):
        await auth.authenticate_websocket(ws)
    ws.close.assert_awaited_once_with(code=4001, reason="Unauthorized")


def test_jwt_socket_stops_receiving_after_membership_removal_at_recheck(socket_app, monkeypatch):
    app, _ = socket_app
    with TestClient(app) as client, _connect(client, "jwt-a") as ws:
        _emit(client, "before", {"projectId": "p-a"})
        assert ws.receive_json()["type"] == "before"

        monkeypatch.setattr(events, "_KEY_RECHECK_SECONDS", 0)
        monkeypatch.setattr(_engine(), "async_session_factory", _no_database)
        ws.send_text("ping")
        assert ws.receive_text() == "pong"
        socket = next(c for c in events._clients.values() if c.user_id == "a-only")
        _wait_for_no_orgs(client, socket)

        _emit(client, "after-removal", {"projectId": "p-a"})
        client.portal.call(events.send_to_user, "a-only", "sentinel", {})
        assert ws.receive_json()["type"] == "sentinel"


def test_key_recheck_adopts_fresh_principal(socket_app, monkeypatch):
    app, _ = socket_app
    with TestClient(app) as client, _connect(client, "acw_a") as key:
        _emit(client, "initial-a", {"projectId": "p-a"})
        assert key.receive_json()["type"] == "initial-a"

        monkeypatch.setattr(events, "_KEY_RECHECK_SECONDS", 0)
        monkeypatch.setattr(
            events,
            "_ws_acw_principal",
            AsyncMock(return_value={"id": "other", "org_id": "B", "api_key_id": "key"}),
        )
        key.send_text("ping")
        assert key.receive_text() == "pong"

        socket = next(iter(events._clients.values()))
        for _ in range(200):
            if socket.user_id == "other" and socket.key_org_id == "B" and socket.org_ids == {"B"}:
                break
            client.portal.call(asyncio.sleep, 0.01)
        assert socket.user_id == "other"
        assert socket.key_org_id == "B"
        assert socket.org_ids == {"B"}

        _emit(client, "dropped-a", {"projectId": "p-a"})
        _emit(client, "rebound-b", {"projectId": "p-b"})
        assert key.receive_json()["type"] == "rebound-b"


def test_start_routes_pass_request_principal_to_emitters(socket_app, monkeypatch):
    from server.background.tasks import spawn  # noqa: PLC0415
    from server.pty import manager as pty_manager_mod  # noqa: PLC0415
    from server.routes import cli_accounts, github  # noqa: PLC0415

    app, _ = socket_app
    monkeypatch.setattr(cli_accounts, "_detect_cli_version", lambda _cli: "1.0.0")
    monkeypatch.setattr(
        pty_manager_mod,
        "get_pty_manager",
        lambda: SimpleNamespace(create_session=lambda **_kw: "term-1", write=lambda *_a: None),
    )

    class ImmediateThread:
        def __init__(self, target, args=(), daemon=None):  # noqa: ARG002
            self._target = target
            self._args = args

        def start(self):
            if self._target is cli_accounts._poll_codex_token:
                _, _loop, principal = self._args
                spawn(
                    events.emit_org_less(
                        principal, "cli-account-auth", {"cli": "codex", "success": True}
                    )
                )

    monkeypatch.setattr(cli_accounts.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(github, "_replica_count", lambda: 1)
    monkeypatch.setattr(github.shutil, "which", lambda _cmd: "/usr/bin/gh")

    async def fake_subprocess(*_args, **_kwargs):
        lines = iter(
            [
                b"First, copy your one-time code: ABCD-1234\n",
                b"https://github.com/login/device\n",
                b"",
            ]
        )

        class Stream:
            async def readline(self):
                return next(lines, b"")

        return SimpleNamespace(
            stderr=Stream(),
            stdout=None,
            returncode=0,
            kill=lambda: None,
            wait=AsyncMock(return_value=0),
        )

    monkeypatch.setattr(github.asyncio, "create_subprocess_exec", fake_subprocess)

    def make_request(principal: Any) -> Request:
        req = Request({"type": "http"})
        req.state.user = principal
        return req

    user_principal = {"id": "owner"}
    service_principal = {"id": "default", "is_service": True}

    with (
        TestClient(app) as client,
        _connect(client, "jwt-owner") as owner,
        _connect(client, "jwt-b") as other,
        _connect(client, "shared") as service,
    ):
        client.portal.call(cli_accounts.start_cli_login, "codex", make_request(user_principal))
        assert owner.receive_json()["type"] == "cli-account-auth"

        client.portal.call(
            cli_accounts.start_cli_login_terminal, "codex", make_request(user_principal)
        )
        assert owner.receive_json()["type"] == "cli-account-auth"

        client.portal.call(github.start_github_auth, make_request(user_principal))
        assert owner.receive_json()["type"] == "github:auth-complete"

        client.portal.call(cli_accounts.start_cli_login, "codex", make_request(service_principal))
        assert service.receive_json()["type"] == "cli-account-auth"

        client.portal.call(
            cli_accounts.start_cli_login_terminal, "codex", make_request(service_principal)
        )
        assert service.receive_json()["type"] == "cli-account-auth"

        client.portal.call(github.start_github_auth, make_request(service_principal))
        assert service.receive_json()["type"] == "github:auth-complete"

        client.portal.call(events.send_to_org, "B", "sentinel", {})
        assert other.receive_json()["type"] == "sentinel"
