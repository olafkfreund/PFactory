"""Org filtering on the unified log and progress WebSockets."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from server.services.task_models import TaskLog, TaskPhase, TaskProgress
from server.websockets import logs, progress, task_access
from starlette.websockets import WebSocketDisconnect

from tests.test_ws_task_access import ENDPOINTS, task_ws_app  # noqa: F401


class Service:
    def __init__(self) -> None:
        self.running = ["p-a:001", "p-b:002", "bare", "p-a:003"]
        self.callbacks: dict[str, Callable[..., Any]] = {}

    def get_running_tasks(self) -> list[str]:
        return self.running.copy()

    def register_log_callback(
        self, task_id: str, callback: Callable[..., Any]
    ) -> Callable[[], None]:
        self.callbacks[task_id] = callback
        return lambda: self.callbacks.pop(task_id, None)

    register_progress_callback = register_log_callback


@pytest.fixture
def all_ws(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Any, Service]:
    app, setting, *_ = request.getfixturevalue("task_ws_app")
    service = Service()
    monkeypatch.setattr(logs, "get_agent_service", lambda: service)
    monkeypatch.setattr(progress, "get_agent_service", lambda: service)
    return app, setting, service


def _connect(client: TestClient, kind: str, token: str) -> Any:
    return client.websocket_connect(f"/ws/{kind}", headers={"authorization": f"Bearer {token}"})


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_member_only_subscribes_to_own_project(all_ws: tuple[Any, Any, Service], kind: str) -> None:
    app, _, service = all_ws
    with TestClient(app) as client, _connect(client, kind, "jwt-a") as ws:
        connected = ws.receive_json()
        assert connected["type"] == "connected"
        if kind == "progress":
            assert connected["running_tasks"] == ["p-a:001", "p-a:003"]
        assert set(service.callbacks) == {"p-a:001", "p-a:003"}


@pytest.mark.parametrize("kind", ENDPOINTS)
@pytest.mark.parametrize("token", ["shared", "acw_b"])
def test_legacy_sees_all_and_other_org_key_only_its_own(
    all_ws: tuple[Any, Any, Service], kind: str, token: str
) -> None:
    app, _, service = all_ws
    with TestClient(app) as client, _connect(client, kind, token) as ws:
        connected = ws.receive_json()
        expected = set(service.running) if token == "shared" else {"p-b:002"}  # noqa: S105
        assert set(service.callbacks) == expected
        if kind == "progress":
            assert connected["running_tasks"] == [t for t in service.running if t in expected]


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_shadow_and_enforced_modes(
    all_ws: tuple[Any, Any, Service], caplog: pytest.LogCaptureFixture, kind: str
) -> None:
    app, setting, service = all_ws
    setting.RBAC_ENFORCE_TASK_ROUTES = False
    with TestClient(app) as client, caplog.at_level(logging.WARNING):
        with _connect(client, kind, "jwt-a") as ws:
            assert ws.receive_json()["type"] == "connected"
            assert set(service.callbacks) == {"p-a:001", "p-a:003", "p-b:002"}
        assert any("p-b" in r.message and "user-a" in r.message for r in caplog.records)
        with _connect(client, kind, "acw_a") as ws:
            assert ws.receive_json()["type"] == "connected"
            assert set(service.callbacks) == {"p-a:001", "p-a:003"}
        setting.RBAC_ENFORCE_TASK_ROUTES = True
        with _connect(client, kind, "jwt-a") as ws:
            assert ws.receive_json()["type"] == "connected"
            assert set(service.callbacks) == {"p-a:001", "p-a:003"}


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_lookup_exception_fails_closed_for_strict_principal(
    all_ws: tuple[Any, Any, Service], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    app, _, service = all_ws
    monkeypatch.setattr(
        task_access, "authorize_project_for_user", AsyncMock(side_effect=RuntimeError("db down"))
    )
    with TestClient(app) as client, _connect(client, kind, "jwt-a") as ws:
        connected = ws.receive_json()
        assert connected["type"] == "connected"
        assert service.callbacks == {}
        if kind == "progress":
            assert connected["running_tasks"] == []


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_shadow_lookup_exception_keeps_stream(
    all_ws: tuple[Any, Any, Service],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    kind: str,
) -> None:
    app, setting, service = all_ws
    setting.RBAC_ENFORCE_TASK_ROUTES = False
    monkeypatch.setattr(
        task_access, "authorize_project_for_user", AsyncMock(side_effect=RuntimeError("db down"))
    )
    with (
        TestClient(app) as client,
        caplog.at_level(logging.WARNING),
        _connect(client, kind, "jwt-a") as ws,
    ):
        assert ws.receive_json()["type"] == "connected"
        assert set(service.callbacks) == {"p-a:001", "p-a:003", "p-b:002"}
    assert any("check failed" in r.message and "user-a" in r.message for r in caplog.records)


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_service_principal_sees_all(
    all_ws: tuple[Any, Any, Service], request: pytest.FixtureRequest, kind: str
) -> None:
    app, _, service = all_ws
    _, _, _, principals = request.getfixturevalue("task_ws_app")
    principals["service"] = {"id": "system", "is_service": True}
    with TestClient(app) as client, _connect(client, kind, "service") as ws:
        connected = ws.receive_json()
        assert set(service.callbacks) == set(service.running)
        if kind == "progress":
            assert connected["running_tasks"] == service.running


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_stream_sends_only_registered_task(
    all_ws: tuple[Any, Any, Service], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    app, _, service = all_ws
    original = service.register_log_callback

    def register(task_id: str, callback: Callable[..., Any]) -> Callable[[], None]:
        unregister = original(task_id, callback)
        if task_id == "p-a:001":
            event = (
                TaskLog(task_id, "visible")
                if kind == "logs"
                else TaskProgress(task_id, TaskPhase.CODING, "visible")
            )
            asyncio.get_running_loop().create_task(callback(event))
        return unregister

    monkeypatch.setattr(service, "register_log_callback", register)
    monkeypatch.setattr(service, "register_progress_callback", register)
    with TestClient(app) as client, _connect(client, kind, "jwt-a") as ws:
        assert ws.receive_json()["type"] == "connected"
        message = ws.receive_json()
        assert message["task_id"] == "p-a:001"
        assert message["type"] == ("log" if kind == "logs" else "progress")


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_revoked_key_closes_on_refresh(
    all_ws: tuple[Any, Any, Service], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    app, _, _ = all_ws
    monkeypatch.setattr(task_access, "_KEY_RECHECK_SECONDS", 0)
    monkeypatch.setattr(task_access, "_ws_acw_principal", AsyncMock(return_value=None))
    with TestClient(app) as client, _connect(client, kind, "acw_a") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4001


@pytest.mark.asyncio
@pytest.mark.usefixtures("all_ws")
async def test_key_recheck_keeps_valid_principal_then_closes_revoked(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, principals = request.getfixturevalue("task_ws_app")
    codes: list[int] = []

    class Socket:
        def __init__(self) -> None:
            self.headers = {"authorization": "Bearer acw_a"}
            self.query_params: dict[str, str] = {}

        async def close(self, code: int) -> None:
            codes.append(code)

    monkeypatch.setattr(task_access, "_KEY_RECHECK_SECONDS", 0)
    monkeypatch.setattr(
        task_access,
        "_ws_acw_principal",
        AsyncMock(side_effect=[{**principals["acw_a"], "id": "fresh"}, None]),
    )
    access = task_access.AllTaskSocketAccess(Socket())
    assert await access.connect()
    assert await access.recheck()
    assert access.user is not None and access.user["id"] == "fresh"
    assert not await access.recheck()
    assert codes == [4001]


@pytest.mark.parametrize("kind", ENDPOINTS)
def test_refresh_precedes_queued_send_and_heartbeat(
    all_ws: tuple[Any, Any, Service],
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    kind: str,
) -> None:
    _, _, service = all_ws
    module = logs if kind == "logs" else progress
    clock = SimpleNamespace(now=0.0, time=lambda: clock.now)
    messages: list[dict[str, Any]] = []
    _, _, memberships, _ = request.getfixturevalue("task_ws_app")

    class Socket:
        def __init__(self) -> None:
            self.headers = {"authorization": "Bearer jwt-a"}
            self.query_params: dict[str, str] = {}

        async def accept(self) -> None:
            return None

        async def send_json(self, message: dict[str, Any]) -> None:
            messages.append(message)

        async def close(self, code: int) -> None:
            raise AssertionError(f"unexpected close {code}")

    calls = 0

    async def wait_for(item: Any, *, timeout: float) -> Any:  # noqa: ARG001, ASYNC109
        nonlocal calls
        item.close()
        calls += 1
        if calls == 1:
            clock.now = 4.9
        elif calls == 2:
            clock.now = 5.1
            memberships.pop(("A", "user-a"), None)
        elif calls == 3:
            clock.now = 10.2
            raise TimeoutError
        else:
            raise WebSocketDisconnect()
        if kind == "logs":
            return TaskLog("p-a:001", "queued")
        return TaskProgress("p-a:001", TaskPhase.CODING, "queued")

    monkeypatch.setattr(
        module,
        "asyncio",
        SimpleNamespace(
            Queue=asyncio.Queue,
            get_running_loop=lambda: clock,
            wait_for=wait_for,
        ),
    )
    handler = module.all_logs_websocket if kind == "logs" else module.all_progress_websocket
    asyncio.run(handler(Socket()))
    assert [message["type"] for message in messages] == [
        "connected",
        "log" if kind == "logs" else "progress",
        "heartbeat",
    ]
    assert service.callbacks == {}
    if kind == "progress":
        assert messages[-1]["running_tasks"] == []
