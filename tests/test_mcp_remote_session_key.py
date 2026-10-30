"""An SSE session only accepts messages from the key that opened it (#946)."""

from __future__ import annotations

import importlib
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

_WEB_SERVER_DIR = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER_DIR))

from server.mcp_remote.auth import AuthenticatedKey  # noqa: E402

SID = "0123456789abcdef0123456789abcdef"


def _key(key_id: str) -> AuthenticatedKey:
    return AuthenticatedKey(key_id=key_id, scopes=frozenset({"mcp:write"}), user_id="u", org_id="o")


async def test_post_from_another_key_is_refused(monkeypatch):
    srv = importlib.import_module("server.mcp_remote.server")
    keys = {"Bearer acw_a": _key("key-a"), "Bearer acw_b": _key("key-b")}

    async def authenticate(header):
        return keys[header]

    seen = {}

    @asynccontextmanager
    async def connect_sse(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        body = f"event: endpoint\r\ndata: /m/?session_id={SID}\r\n\r\n"
        await send({"type": "http.response.body", "body": body.encode()})
        seen["bound"] = srv._session_keys.get(SID)
        yield None, None

    async def run(*_args):
        pass

    delivered = []

    async def handle_post_message(scope, _receive, _send):
        delivered.append(scope["query_string"])

    monkeypatch.setattr(srv, "authenticate", authenticate)
    monkeypatch.setattr(srv._sse_transport, "connect_sse", connect_sse)
    monkeypatch.setattr(srv._sse_transport, "handle_post_message", handle_post_message)
    monkeypatch.setattr(srv._mcp_server, "run", AsyncMock(side_effect=run))

    sent = []

    async def raw_send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/mcp-remote/sse",
        "headers": [(b"authorization", b"Bearer acw_a")],
        "query_string": b"",
    }
    await srv.sse_endpoint(Request(scope, AsyncMock(), raw_send))
    assert seen["bound"] == "key-a"
    assert len(sent) == 2  # the wrapper still forwards every message
    assert SID not in srv._session_keys  # dropped when the stream closes

    app = FastAPI()
    app.include_router(srv.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        srv._session_keys[SID] = "key-a"
        try:
            url = f"/api/mcp-remote/messages/?session_id={SID}"
            other = await client.post(url, headers={"Authorization": "Bearer acw_b"})
            own = await client.post(url, headers={"Authorization": "Bearer acw_a"})
            unknown = await client.post(
                "/api/mcp-remote/messages/?session_id=" + "f" * 32,
                headers={"Authorization": "Bearer acw_a"},
            )
        finally:
            srv._session_keys.pop(SID, None)

    assert other.status_code == 403
    assert own.status_code == 200
    assert unknown.status_code == 403
    assert len(delivered) == 1  # only the session's own key got through
