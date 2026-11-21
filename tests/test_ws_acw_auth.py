"""Scoped personal keys on the events WebSocket."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1] / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from server import auth  # noqa: E402
from server.websockets import events, relay  # noqa: E402


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def all(self):
        return self.value


class _Session:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, _stmt):
        value = next(self.rows)
        if isinstance(value, Exception):
            raise value
        return _Result(value)


def _key(scopes="api", expires_at=None):
    return SimpleNamespace(
        id="key-1",
        user_id="user-1",
        org_id="A",
        scopes=scopes,
        expires_at=expires_at,
        last_used_at=None,
    )


def _user():
    return SimpleNamespace(id="user-1", email="user@example.test", role="user", is_active=True)


def _stub_key_db(monkeypatch, rows):
    async def get_db():
        yield _Session(rows)

    # Resolved at run time, like tests/test_api_key_auth.py: other tests swap
    # server.database.engine in sys.modules, so an import-time handle goes stale.
    import server.database.engine as engine_mod  # noqa: PLC0415

    monkeypatch.setattr(engine_mod, "get_db", get_db)


def _ws(token):
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}"},
        query_params={},
        close=AsyncMock(),
    )


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    shared = "shared"
    settings = SimpleNamespace(
        DISABLE_AUTH=False, API_TOKEN=shared, LEGACY_SHARED_TOKEN_FULL_ACCESS=True
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "_try_decode_jwt", lambda _raw: None)
    return settings


@pytest.mark.asyncio
async def test_owned_api_key_matches_rest_principal(monkeypatch):
    key = _key()
    _stub_key_db(monkeypatch, [key, "member", _user()])
    rest_principal = await auth._try_authenticate_personal_key("acw_owned")
    assert await auth.authenticate_websocket(_ws("acw_owned")) == rest_principal
    assert rest_principal == {
        "id": "user-1",
        "email": "user@example.test",
        "role": "user",
        "api_key_id": "key-1",
        "org_id": "A",
        "scopes": ("api",),
    }
    assert key.last_used_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [None],
        # Full rows: only the check under test may reject (else row exhaustion would).
        [_key(expires_at=datetime.now(UTC) - timedelta(days=1)), "member", _user()],
        [_key(scopes="mcp:read"), "member", _user()],
        [RuntimeError("database unavailable")],
    ],
    ids=["unknown", "expired", "missing-api-scope", "validator-raises"],
)
async def test_invalid_api_key_closes_4001(monkeypatch, rows):
    _stub_key_db(monkeypatch, rows)
    ws = _ws("acw_invalid")
    with pytest.raises(auth.WebSocketAuthError):
        await auth.authenticate_websocket(ws)
    ws.close.assert_awaited_once_with(code=4001, reason="Unauthorized")


@pytest.mark.asyncio
async def test_jwt_and_legacy_paths_unchanged(monkeypatch):
    def decode(raw):
        if raw == "jwt":
            return {"sub": "jwt-user", "email": "jwt@example.test", "role": "admin"}
        return None

    monkeypatch.setattr(auth, "_try_decode_jwt", decode)
    monkeypatch.setattr(
        auth, "_ws_acw_principal", AsyncMock(side_effect=AssertionError("key lookup"))
    )
    assert await auth.authenticate_websocket(_ws("jwt")) == {
        "id": "jwt-user",
        "email": "jwt@example.test",
        "role": "admin",
    }
    assert await auth.authenticate_websocket(_ws("shared")) == {
        "id": "default",
        "is_service": True,
    }


def _stub_memberships(monkeypatch, rows):
    queried = asyncio.Event()

    class _MembershipSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _stmt):
            queried.set()
            if isinstance(rows, Exception):
                raise rows
            return _Result(rows)

    import server.database.engine as engine_mod  # noqa: PLC0415

    monkeypatch.setattr(engine_mod, "async_session_factory", _MembershipSession)
    return queried


class _EventsWebSocket:
    def __init__(self):
        self.headers = {"authorization": "Bearer acw_owned"}
        self.query_params = {}
        self.send_text = AsyncMock()
        self.close = AsyncMock()

    async def accept(self):
        pass

    async def receive_text(self):
        await asyncio.sleep(3600)


async def _start_endpoint(monkeypatch, org_ids):
    monkeypatch.setattr(relay, "publish", AsyncMock())
    ws = _EventsWebSocket()
    task = asyncio.create_task(events.events_websocket(ws))
    await _wait_for_client(org_ids)
    return ws, task


async def _wait_for_client(org_ids):
    for _ in range(100):
        if any(client.org_ids == org_ids for client in events._clients.values()):
            return
        await asyncio.sleep(0.01)
    pytest.fail("WebSocket client did not load org memberships")


@pytest.mark.asyncio
async def test_bound_key_receives_only_its_org(monkeypatch):
    _stub_key_db(monkeypatch, [_key(), "member", _user()])
    _stub_memberships(monkeypatch, [("A",), ("B",)])
    ws, task = await _start_endpoint(monkeypatch, {"A"})
    try:
        await events.send_to_org("B", "wrong-org", {})
        await events.send_to_org("A", "right-org", {})
        assert ws.send_text.await_count == 1
        assert '"type": "right-org"' in ws.send_text.await_args.args[0]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_initial_membership_failure_closes_key_socket(monkeypatch, caplog):
    _stub_key_db(monkeypatch, [_key(), "member", _user()])
    _stub_memberships(monkeypatch, RuntimeError("membership unavailable"))
    monkeypatch.setattr(relay, "publish", AsyncMock())
    ws = _EventsWebSocket()
    await asyncio.wait_for(events.events_websocket(ws), 2)
    ws.close.assert_awaited_once_with(code=1011)
    assert not events._clients
    assert "Could not load org memberships for WS client: RuntimeError" in caplog.text
    assert "acw_owned" not in caplog.text


@pytest.mark.asyncio
async def test_recheck_membership_failure_closes_key_socket(monkeypatch):
    _stub_key_db(monkeypatch, [_key(), "member", _user()])
    calls = 0

    class _FlakySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _stmt):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError("membership unavailable")
            return _Result([("A",)])

    import server.database.engine as engine_mod  # noqa: PLC0415

    monkeypatch.setattr(engine_mod, "async_session_factory", _FlakySession)
    monkeypatch.setattr(events, "_KEY_RECHECK_SECONDS", 0)
    monkeypatch.setattr(
        events,
        "_ws_acw_principal",
        AsyncMock(return_value={"id": "user-1", "org_id": "A", "api_key_id": "key-1"}),
    )
    monkeypatch.setattr(relay, "publish", AsyncMock())
    ws = _EventsWebSocket()
    await asyncio.wait_for(events.events_websocket(ws), 2)
    ws.close.assert_awaited_once_with(code=1011)
    assert not events._clients


@pytest.mark.asyncio
async def test_jwt_socket_stays_open_when_membership_fails(monkeypatch):
    monkeypatch.setattr(auth, "_try_decode_jwt", lambda raw: {"sub": "jwt-user", "email": "e"})
    _stub_memberships(monkeypatch, RuntimeError("membership unavailable"))
    monkeypatch.setattr(relay, "publish", AsyncMock())
    ws = _EventsWebSocket()
    ws.headers = {"authorization": "Bearer jwt"}
    task = asyncio.create_task(events.events_websocket(ws))
    try:
        await _wait_for_client(set())
        await events.send_to_org("A", "event", {})
        ws.send_text.assert_not_awaited()
        ws.close.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_validator_exception_is_logged_without_the_token(monkeypatch, caplog):
    _stub_key_db(monkeypatch, [RuntimeError("database unavailable")])
    assert await auth._ws_acw_principal("acw_sentinel_secret") is None
    assert "WS key validation failed: RuntimeError" in caplog.text
    assert "acw_sentinel_secret" not in caplog.text
