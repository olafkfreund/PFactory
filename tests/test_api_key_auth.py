"""Personal API-key (acw_) authentication on /api/* (Issue #93).

The mint/list/revoke backend (routes/api_keys.py) and the Settings UI already
existed; the missing piece was ``TokenAuthMiddleware`` accepting an ``acw_``
key as a Bearer on the general REST surface. These tests pin that behaviour:

- only keys carrying the ``api`` scope unlock /api/* (MCP-only keys stay
  scope-isolated),
- expired keys are rejected,
- unknown keys / non-acw_ tokens fall through,
- a valid key resolves to its owner and bumps ``last_used_at``.

No network, no real DB — ``get_db`` is stubbed with a fake async session.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

pytest.importorskip("fastapi")

from server import auth as auth_mod  # noqa: E402

# ---------------------------------------------------------------------------
# Fake async DB session
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns the queued rows from successive ``execute`` calls in order."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, _stmt):
        return _FakeResult(self._rows.pop(0))


def _install_fake_db(monkeypatch, rows):
    async def fake_get_db():
        yield _FakeSession(rows)

    # The helper imports ``get_db`` lazily from server.database.engine.
    import server.database.engine as engine_mod

    monkeypatch.setattr(engine_mod, "get_db", fake_get_db)


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _api_key(scopes: str, *, expires_at=None, user_id="user-1"):
    return SimpleNamespace(
        id="key-1",
        user_id=user_id,
        scopes=scopes,
        expires_at=expires_at,
        last_used_at=None,
    )


def _user(active=True):
    return SimpleNamespace(
        id="user-1", email="ada@example.com", role="user", is_active=active
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_acw_token_returns_none(monkeypatch):
    # JWTs / legacy tokens never reach the DB lookup.
    assert await auth_mod._try_authenticate_api_key("eyJhbGci...") is None


@pytest.mark.asyncio
async def test_api_scoped_key_authenticates_owner(monkeypatch):
    raw = "acw_validtoken"
    _install_fake_db(monkeypatch, [_api_key("api,mcp:read"), _user()])
    result = await auth_mod._try_authenticate_api_key(raw)
    assert result == {"id": "user-1", "email": "ada@example.com", "role": "user"}


@pytest.mark.asyncio
async def test_mcp_only_key_is_rejected_on_rest_api(monkeypatch):
    # A key minted purely for the MCP control plane must NOT unlock /api/*.
    _install_fake_db(monkeypatch, [_api_key("mcp:read,task:write"), _user()])
    assert await auth_mod._try_authenticate_api_key("acw_mcponly") is None


@pytest.mark.asyncio
async def test_expired_key_is_rejected(monkeypatch):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    _install_fake_db(monkeypatch, [_api_key("api", expires_at=past), _user()])
    assert await auth_mod._try_authenticate_api_key("acw_expired") is None


@pytest.mark.asyncio
async def test_unexpired_key_is_accepted(monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    _install_fake_db(monkeypatch, [_api_key("api", expires_at=future), _user()])
    result = await auth_mod._try_authenticate_api_key("acw_future")
    assert result is not None
    assert result["id"] == "user-1"


@pytest.mark.asyncio
async def test_unknown_key_returns_none(monkeypatch):
    _install_fake_db(monkeypatch, [None])  # no matching ApiKey row
    assert await auth_mod._try_authenticate_api_key("acw_unknown") is None


@pytest.mark.asyncio
async def test_inactive_owner_returns_none(monkeypatch):
    _install_fake_db(monkeypatch, [_api_key("api"), _user(active=False)])
    assert await auth_mod._try_authenticate_api_key("acw_orphan") is None


@pytest.mark.asyncio
async def test_last_used_is_bumped_on_success(monkeypatch):
    key = _api_key("api")
    session_holder = {}

    async def fake_get_db():
        sess = _FakeSession([key, _user()])
        session_holder["sess"] = sess
        yield sess

    import server.database.engine as engine_mod

    monkeypatch.setattr(engine_mod, "get_db", fake_get_db)

    await auth_mod._try_authenticate_api_key("acw_track")
    assert key.last_used_at is not None
    session_holder["sess"].commit.assert_awaited()


def test_api_scope_constant_is_api():
    # The UI mints with scope id "api"; the gate must match it.
    assert auth_mod.API_SCOPE == "api"
    assert auth_mod.API_KEY_PREFIX == "acw_"


def test_digest_matches_api_keys_route():
    # Middleware hashing must agree with how routes/api_keys.py stores keys,
    # or no key would ever match.
    raw = "acw_abc123"
    assert auth_mod._hash_api_key(raw) == _digest(raw)


# ---------------------------------------------------------------------------
# Middleware ordering: legacy shared token is checked BEFORE the acw_ DB lookup
# so an acw_-shaped APP_API_TOKEN still authenticates when its DB-backed key is
# absent (e.g. api_keys wiped on a cluster rebuild). Regression for the
# CFactory -> PFactory /api 401 (2026-06-25).
# ---------------------------------------------------------------------------


def _make_api_request(token: str):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/projects",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "query_string": b"",
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_acw_shaped_shared_token_falls_back_to_legacy(monkeypatch):
    shared = "acw_sharedserviceprincipal"
    monkeypatch.setattr(
        auth_mod,
        "get_settings",
        lambda: SimpleNamespace(DISABLE_AUTH=False, API_TOKEN=shared),
    )
    _install_fake_db(monkeypatch, [None])  # DB-backed acw_ key absent (wiped)

    mw = auth_mod.TokenAuthMiddleware(app=None)
    request = _make_api_request(shared)
    called = {}

    async def call_next(_req):
        called["downstream"] = True
        return SimpleNamespace(status_code=200)

    resp = await mw.dispatch(request, call_next)
    assert called.get("downstream"), "legacy fallback must authenticate the shared token"
    assert resp.status_code == 200
    assert request.state.user["id"] == "default"


@pytest.mark.asyncio
async def test_bad_acw_token_still_rejected(monkeypatch):
    monkeypatch.setattr(
        auth_mod,
        "get_settings",
        lambda: SimpleNamespace(DISABLE_AUTH=False, API_TOKEN="a-different-secret"),  # noqa: S106
    )
    _install_fake_db(monkeypatch, [None])  # unknown acw_ key

    mw = auth_mod.TokenAuthMiddleware(app=None)
    request = _make_api_request("acw_bogus")
    called = {}

    async def call_next(_req):
        called["downstream"] = True
        return SimpleNamespace(status_code=200)

    resp = await mw.dispatch(request, call_next)
    assert not called.get("downstream"), "a bad acw_ key must not reach downstream"
    assert resp.status_code == 401
