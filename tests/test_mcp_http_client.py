"""Tests for ``apps/backend/agents/tools_pkg/http_client.py``.

The MCP control-plane tools all funnel through ``request()``, so the
operator-actionable error paths (server-down, token-rejected, token-missing,
empty-token, timeout) need exhaustive coverage. Behaviour-level tests
only; the wire protocol is httpx's responsibility.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from agents.tools_pkg import http_client as hc


@pytest.fixture(autouse=True)
async def isolate(monkeypatch, tmp_path):
    """Each test gets a fresh client + clean env."""
    monkeypatch.delenv("PFACTORY_API_URL", raising=False)
    monkeypatch.delenv("PFACTORY_API_TOKEN_FILE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    await hc.reset()
    yield
    await hc.reset()


def _write_token(tmp_path: Path, value: str = "test-token") -> Path:
    target = tmp_path / ".pfactory" / ".token"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value)
    return target


async def _make_mock_transport(handler):
    """Return a configured httpx.AsyncClient + monkeypatch hook.

    The state singleton lazily creates an AsyncClient on first request;
    we replace it here with a MockTransport-backed client so tests don't
    talk to a real socket.
    """
    return httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )


async def test_token_missing_returns_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PFACTORY_API_TOKEN_FILE", str(tmp_path / "nope"))
    with pytest.raises(hc.MCPHTTPError) as exc_info:
        await hc.request("GET", "/api/tasks")
    assert "not found" in str(exc_info.value).lower()
    assert "regenerate" in str(exc_info.value).lower()


async def test_token_empty_returns_actionable_error(tmp_path, monkeypatch):
    token_path = _write_token(tmp_path, value="   \n")
    monkeypatch.setenv("PFACTORY_API_TOKEN_FILE", str(token_path))
    with pytest.raises(hc.MCPHTTPError) as exc_info:
        await hc.request("GET", "/api/tasks")
    assert "empty" in str(exc_info.value).lower()


async def test_request_sends_bearer_token(tmp_path, monkeypatch):
    _write_token(tmp_path, value="ghost-token-xyz")
    monkeypatch.setenv("PFACTORY_API_TOKEN_FILE", str(tmp_path / ".pfactory" / ".token"))

    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(hc._state, "_client", await _make_mock_transport(handler))
    monkeypatch.setattr(hc._state, "_base_url", hc._state.base_url())

    result = await hc.request("GET", "/api/tasks")
    assert result == {"ok": True}
    assert captured["auth"] == "Bearer ghost-token-xyz"


async def test_401_returns_actionable_error(tmp_path, monkeypatch):
    _write_token(tmp_path)
    monkeypatch.setenv("PFACTORY_API_TOKEN_FILE", str(tmp_path / ".pfactory" / ".token"))

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    monkeypatch.setattr(hc._state, "_client", await _make_mock_transport(handler))
    monkeypatch.setattr(hc._state, "_base_url", hc._state.base_url())

    with pytest.raises(hc.MCPHTTPError) as exc_info:
        await hc.request("GET", "/api/tasks")
    # After Issue #154 the 401 error guides the user toward minting an
    # acw_ key, not regenerating the legacy admin token.
    assert "rejected" in str(exc_info.value).lower()
    assert "mint" in str(exc_info.value).lower()
    assert "settings" in str(exc_info.value).lower()


async def test_404_returns_resource_not_found(tmp_path, monkeypatch):
    _write_token(tmp_path)
    monkeypatch.setenv("PFACTORY_API_TOKEN_FILE", str(tmp_path / ".pfactory" / ".token"))

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    monkeypatch.setattr(hc._state, "_client", await _make_mock_transport(handler))
    monkeypatch.setattr(hc._state, "_base_url", hc._state.base_url())

    with pytest.raises(hc.MCPHTTPError) as exc_info:
        await hc.request("GET", "/api/tasks/does-not-exist")
    assert "404" in str(exc_info.value)
    assert "not found" in str(exc_info.value).lower()


async def test_5xx_surfaces_truncated_body(tmp_path, monkeypatch):
    _write_token(tmp_path)
    monkeypatch.setenv("PFACTORY_API_TOKEN_FILE", str(tmp_path / ".pfactory" / ".token"))

    huge = "x" * 5000  # forces truncation at 500

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=huge)

    monkeypatch.setattr(hc._state, "_client", await _make_mock_transport(handler))
    monkeypatch.setattr(hc._state, "_base_url", hc._state.base_url())

    with pytest.raises(hc.MCPHTTPError) as exc_info:
        await hc.request("GET", "/api/tasks")
    msg = str(exc_info.value)
    assert "503" in msg
    # Body was truncated, not full 5000 chars
    assert len(msg) < 1500


async def test_connection_refused_returns_start_guidance(tmp_path, monkeypatch):
    _write_token(tmp_path)
    monkeypatch.setenv("PFACTORY_API_TOKEN_FILE", str(tmp_path / ".pfactory" / ".token"))

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(hc._state, "_client", await _make_mock_transport(handler))
    monkeypatch.setattr(hc._state, "_base_url", hc._state.base_url())

    with pytest.raises(hc.MCPHTTPError) as exc_info:
        await hc.request("GET", "/api/tasks")
    msg = str(exc_info.value)
    assert "not reachable" in msg.lower()
    assert "python -m server.main" in msg


async def test_api_url_env_override(tmp_path, monkeypatch):
    _write_token(tmp_path)
    monkeypatch.setenv("PFACTORY_API_TOKEN_FILE", str(tmp_path / ".pfactory" / ".token"))
    monkeypatch.setenv("PFACTORY_API_URL", "http://custom-host:9999/")
    assert hc._state.base_url() == "http://custom-host:9999"


async def test_token_rotation_picked_up_without_restart(tmp_path, monkeypatch):
    token_path = _write_token(tmp_path, value="first-token")
    monkeypatch.setenv("PFACTORY_API_TOKEN_FILE", str(token_path))

    captured = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req.headers.get("authorization"))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(hc._state, "_client", await _make_mock_transport(handler))
    monkeypatch.setattr(hc._state, "_base_url", hc._state.base_url())

    await hc.request("GET", "/api/tasks")
    # Rotate the token file
    token_path.write_text("second-token")
    await hc.request("GET", "/api/tasks")

    assert captured == ["Bearer first-token", "Bearer second-token"]
