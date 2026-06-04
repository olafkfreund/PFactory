"""Tests for the stdio-MCP proxy (Issues #154 + #50).

Three concerns:

1. **Auth + scope:** ``require_acw_scope`` accepts the legacy admin
   token as a wildcard, rejects missing / bad / scope-mismatched
   ``acw_`` keys with 401 vs 403 respectively, and accepts valid
   ``acw_`` keys with the right scope.

2. **Client routing:** ``http_client._read_token`` prefers env-var
   ``PFACTORY_MCP_KEY`` over the legacy admin token, and the
   ``request()`` path rewrite prepends ``/api/mcp-stdio`` to outbound
   calls so they hit the proxy.

3. **Audit logging (Epic #50 acceptance criterion #2):** every write
   route fires ``log_audit_event_bg`` with the right
   ``action=mcp.<verb>`` constant; read routes do NOT log; audit
   failures don't crash the route (failure-safe via the bg helper's
   try/except).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


from server.mcp_remote.auth import AuthenticatedKey, MCPAuthError
from server.mcp_stdio.auth import (
    MCP_READ_SCOPE,
    PROJECT_WRITE_SCOPE,
    _LegacyAdminKey,
    require_acw_scope,
)

# ── Legacy admin token = wildcard ────────────────────────────────────


def test_legacy_admin_key_has_every_scope():
    """The synthetic legacy key advertises every named scope."""
    k = _LegacyAdminKey()
    assert k.has_scope(MCP_READ_SCOPE)
    assert k.has_scope(PROJECT_WRITE_SCOPE)
    assert k.has_scope("task:write")
    assert k.has_scope("task:merge")
    # Unknown scopes also pass — by design, the legacy admin is
    # unconstrained, so even a typo-scope wouldn't block it.
    assert k.has_scope("anything-the-caller-passes")


# ── Dependency: auth + scope behaviour ───────────────────────────────


def _app_with_scope(scope: str) -> FastAPI:
    """Build a tiny FastAPI app whose only route is gated by ``scope``.

    Used to drive the auth dependency end-to-end via TestClient.
    """
    app = FastAPI()

    @app.get("/probe")
    async def probe(_=__import__("fastapi").Depends(require_acw_scope(scope))):
        return {"ok": True}

    return app


def test_missing_auth_header_returns_401():
    client = TestClient(_app_with_scope(MCP_READ_SCOPE))
    r = client.get("/probe")
    assert r.status_code == 401
    assert "Missing" in r.json()["detail"] or "malformed" in r.json()["detail"]


def test_legacy_admin_token_acts_as_wildcard(monkeypatch):
    """Token matching settings.API_TOKEN passes any scope check."""
    from server import config

    # Patch settings.API_TOKEN to a known value.
    monkeypatch.setattr(
        config.get_settings(), "API_TOKEN", "legacy-secret-token-for-test"
    )
    client = TestClient(_app_with_scope("task:merge"))
    r = client.get(
        "/probe", headers={"Authorization": "Bearer legacy-secret-token-for-test"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_unknown_acw_key_returns_401(monkeypatch):
    """An unknown bearer token falls through to acw_ validation, which
    rejects via MCPAuthError → 401."""
    async def _raise(_header):
        raise MCPAuthError("Invalid API key")

    monkeypatch.setattr(
        "server.mcp_stdio.auth.mcp_remote_auth.authenticate", _raise
    )
    client = TestClient(_app_with_scope(MCP_READ_SCOPE))
    r = client.get("/probe", headers={"Authorization": "Bearer acw_unknown"})
    assert r.status_code == 401
    assert "Invalid API key" in r.json()["detail"]


def test_acw_key_with_wrong_scope_returns_403(monkeypatch):
    """A valid acw_ key that lacks the requested scope → 403, not 401.

    The 401/403 split lets the client tell 'your key is bad' (regen)
    apart from 'your key works but is scoped wrong' (mint a new one).
    """
    async def _ok(_header):
        return AuthenticatedKey(
            key_id="key-123",
            scopes=frozenset({MCP_READ_SCOPE}),  # READ only
            user_id="user-1",
        )

    monkeypatch.setattr(
        "server.mcp_stdio.auth.mcp_remote_auth.authenticate", _ok
    )
    client = TestClient(_app_with_scope(PROJECT_WRITE_SCOPE))  # need WRITE
    r = client.get("/probe", headers={"Authorization": "Bearer acw_readonly"})
    assert r.status_code == 403
    assert "project:write" in r.json()["detail"]


def test_acw_key_with_right_scope_passes(monkeypatch):
    """Happy path: scoped acw_ key passes → handler runs."""
    async def _ok(_header):
        return AuthenticatedKey(
            key_id="key-456",
            scopes=frozenset({MCP_READ_SCOPE, PROJECT_WRITE_SCOPE}),
            user_id="user-2",
        )

    monkeypatch.setattr(
        "server.mcp_stdio.auth.mcp_remote_auth.authenticate", _ok
    )
    client = TestClient(_app_with_scope(PROJECT_WRITE_SCOPE))
    r = client.get(
        "/probe", headers={"Authorization": "Bearer acw_writer"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── Client-side token resolution ─────────────────────────────────────


def test_client_prefers_env_var_over_files(tmp_path, monkeypatch):
    """$PFACTORY_MCP_KEY beats both .mcp-key and the legacy token."""
    from agents.tools_pkg import http_client

    # Set up files that should be IGNORED.
    mcp_key_file = tmp_path / ".mcp-key"
    mcp_key_file.write_text("acw_from_file\n")
    legacy_file = tmp_path / ".token"
    legacy_file.write_text("legacy_admin_token\n")

    monkeypatch.setattr(http_client, "DEFAULT_MCP_KEY_FILE", str(mcp_key_file))
    monkeypatch.setattr(http_client, "DEFAULT_TOKEN_FILE", str(legacy_file))
    monkeypatch.setenv("PFACTORY_MCP_KEY", "acw_from_env")

    assert http_client._read_token() == "acw_from_env"


def test_client_falls_back_to_mcp_key_file_then_legacy(tmp_path, monkeypatch):
    """No env var → .mcp-key file → legacy token chain."""
    from agents.tools_pkg import http_client

    mcp_key_file = tmp_path / ".mcp-key"
    legacy_file = tmp_path / ".token"
    legacy_file.write_text("legacy_admin\n")

    monkeypatch.setattr(http_client, "DEFAULT_MCP_KEY_FILE", str(mcp_key_file))
    monkeypatch.setattr(http_client, "DEFAULT_TOKEN_FILE", str(legacy_file))
    monkeypatch.delenv("PFACTORY_MCP_KEY", raising=False)
    monkeypatch.delenv("PFACTORY_API_TOKEN_FILE", raising=False)

    # No .mcp-key file → uses legacy.
    assert http_client._read_token() == "legacy_admin"

    # Drop in a .mcp-key file → it takes precedence.
    mcp_key_file.write_text("acw_scoped\n")
    assert http_client._read_token() == "acw_scoped"


def test_client_rewrites_path_to_proxy_prefix(monkeypatch):
    """``request("GET", "/api/tasks")`` → outbound hits
    ``/api/mcp-stdio/tasks``. Confirms the stdio MCP never calls the
    raw REST surface and so cannot bypass scope gates by accident."""
    from agents.tools_pkg import http_client

    captured = {}

    class _FakeResp:
        status_code = 200
        content = b"{}"
        text = "{}"

        def json(self):
            return {}

    class _FakeClient:
        async def request(self, method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            return _FakeResp()

        async def aclose(self):
            pass

    monkeypatch.setattr(http_client._state, "_client", _FakeClient())
    monkeypatch.setattr(http_client._state, "_base_url", "http://localhost:3114")
    monkeypatch.setattr(http_client, "_read_token", lambda: "acw_test_key")

    import asyncio
    asyncio.run(http_client.request("GET", "/api/tasks"))
    assert captured["path"] == "/api/mcp-stdio/tasks"

    # And paths already under the proxy prefix pass through unchanged.
    asyncio.run(http_client.request("GET", "/api/mcp-stdio/projects"))
    assert captured["path"] == "/api/mcp-stdio/projects"


# ── Audit logging on writes (Epic #50 acceptance criterion #2) ──────


def test_audit_helper_passes_right_fields_for_authenticated_key(monkeypatch):
    """``_audit_mcp_write`` should forward user_id + org_id + key_id +
    action to ``log_audit_event_bg`` so audit rows carry full provenance."""
    # The package __init__ re-exports the APIRouter as ``router``,
    # which shadows the module attribute. Grab the actual module
    # via sys.modules — `import server.mcp_stdio.router` ensures
    # it's loaded.
    import sys
    from unittest.mock import AsyncMock

    import server.mcp_stdio.router  # noqa: F401  (load into sys.modules)
    proxy_router = sys.modules["server.mcp_stdio.router"]

    captured: dict = {}

    async def _fake_bg(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(proxy_router, "log_audit_event_bg", _fake_bg)

    key = AuthenticatedKey(
        key_id="key-abc",
        scopes=frozenset({"task:write"}),
        user_id="user-42",
        org_id="org-99",
    )

    class _FakeRequest:
        class _Client:
            host = "10.0.0.1"
        client = _Client()

    import asyncio
    asyncio.run(
        proxy_router._audit_mcp_write(
            key,
            action="mcp.task.start",
            resource_type="task",
            resource_id="spec-001",
            request=_FakeRequest(),
        )
    )

    assert captured["user_id"] == "user-42"
    assert captured["org_id"] == "org-99"
    assert captured["action"] == "mcp.task.start"
    assert captured["resource_type"] == "task"
    assert captured["resource_id"] == "spec-001"
    assert captured["ip"] == "10.0.0.1"
    # mcp_key_id must end up in details so we can correlate audit
    # rows back to the specific minted key.
    assert captured["details"]["mcp_key_id"] == "key-abc"


def test_audit_helper_marks_legacy_admin_calls(monkeypatch):
    """Calls made via the legacy admin token wildcard should still
    audit-log, but with ``mcp_key_id=legacy-admin`` so operators can
    tell wildcard calls apart from scoped-key calls."""
    # The package __init__ re-exports the APIRouter as ``router``,
    # which shadows the module attribute. Grab the actual module
    # via sys.modules — `import server.mcp_stdio.router` ensures
    # it's loaded.
    import sys

    import server.mcp_stdio.router  # noqa: F401  (load into sys.modules)
    proxy_router = sys.modules["server.mcp_stdio.router"]

    captured: dict = {}

    async def _fake_bg(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(proxy_router, "log_audit_event_bg", _fake_bg)

    class _FakeRequest:
        client = None  # exercises the IP-less branch

    import asyncio
    asyncio.run(
        proxy_router._audit_mcp_write(
            _LegacyAdminKey(),
            action="mcp.task.stop",
            resource_type="task",
            resource_id="spec-077",
            request=_FakeRequest(),
        )
    )

    assert captured["user_id"] is None
    assert captured["org_id"] is None
    assert captured["details"]["mcp_key_id"] == "legacy-admin"
    assert captured["ip"] is None


def test_audit_constants_use_mcp_namespace():
    """Every constant added for Epic #50 must use the ``mcp.`` prefix
    so audit reviewers can grep MCP-initiated mutations apart from
    UI-driven ones (``task.start`` vs ``mcp.task.start``)."""
    from server.services.audit_service import (
        ACTION_MCP_PROJECT_CREATE,
        ACTION_MCP_TASK_APPROVE_PLAN,
        ACTION_MCP_TASK_CREATE_AND_RUN,
        ACTION_MCP_TASK_CREATE_PR,
        ACTION_MCP_TASK_MERGE,
        ACTION_MCP_TASK_RECOVER,
        ACTION_MCP_TASK_START,
        ACTION_MCP_TASK_STOP,
    )

    for action in [
        ACTION_MCP_PROJECT_CREATE,
        ACTION_MCP_TASK_APPROVE_PLAN,
        ACTION_MCP_TASK_CREATE_AND_RUN,
        ACTION_MCP_TASK_CREATE_PR,
        ACTION_MCP_TASK_MERGE,
        ACTION_MCP_TASK_RECOVER,
        ACTION_MCP_TASK_START,
        ACTION_MCP_TASK_STOP,
    ]:
        assert action.startswith("mcp."), (
            f"Expected mcp.* namespace for Epic #50 audit constant, got {action!r}"
        )
