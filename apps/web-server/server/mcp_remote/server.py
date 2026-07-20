"""FastAPI routes for the Remote HTTP+SSE MCP server.

Two routes:
  * ``GET  /api/mcp/sse``         — SSE event stream the MCP client subscribes to
  * ``POST /api/mcp/messages/``   — client-to-server JSON-RPC messages

Both routes bypass ``TokenAuthMiddleware``'s scheme — the global middleware
expects JWT or the legacy ``API_TOKEN``; MCP clients send their ``acw_``
key + scopes instead. The mount in ``main.py`` adds ``/api/mcp/`` to
``PUBLIC_PREFIXES`` so the middleware lets the request through and our
own ``mcp_remote.auth.authenticate`` does the work.

The MCP server delegates ``tools/list`` and ``tools/call`` to
``mcp_remote.tools``. Anything else (resources, prompts, sampling)
isn't supported in V1; the SDK returns the standard JSON-RPC
``method not found`` error.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool

from .auth import MCPAuthError, authenticate
from .tools import dispatch_tool_call, get_tool_definitions

logger = logging.getLogger(__name__)


# The MCP SDK's ``Server`` instance is process-singleton. Tools / handlers
# are registered once at module import time; per-request state (the
# AuthenticatedKey) is threaded through via contextvars set inside the
# request handlers below.
_mcp_server: Server = Server("pfactory-remote", version="1.0.0")


# ContextVar for the current request's authenticated key. The tool
# handler reads this — we can't pass it through the MCP SDK's call
# signature.
import contextvars

_current_key: contextvars.ContextVar = contextvars.ContextVar(
    "mcp_remote_current_key", default=None
)


@_mcp_server.list_tools()
async def _list_tools() -> list[Tool]:
    """Return the static tool catalog. No auth check — discovery is open."""
    defs = get_tool_definitions()
    return [
        Tool(
            name=d["name"],
            description=d["description"],
            inputSchema=d["inputSchema"],
        )
        for d in defs
    ]


@_mcp_server.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    """Dispatch a tool call — auth + scope check + result envelope."""
    key = _current_key.get()
    if key is None:
        # Shouldn't happen — the route handler sets it before delegating.
        # If it does, treat as unauthenticated so we never accidentally
        # serve a tool without a key.
        return [TextContent(type="text", text="Error: missing auth context")]
    result = await dispatch_tool_call(name, arguments, key)
    # Translate our ``{content: [...]}`` envelope back into the SDK's
    # TextContent list shape that ``call_tool`` expects.
    blocks = result.get("content", [])
    return [TextContent(type="text", text=b.get("text", "")) for b in blocks]


# SSE transport instance — endpoint is the path where the client posts
# messages back to. Matches the path the messages route handles.
# Note: ``/api/mcp/`` is already used by ``routes/git.py::mcp_router``
# for the MCP-Git-provider feature; we use ``/api/mcp-remote/`` to keep
# the two surfaces independent.
_sse_transport = SseServerTransport("/api/mcp-remote/messages/")


# ---------------------------------------------------------------------------
# FastAPI router — mounted by main.py when ``is_enabled()`` is true
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/mcp-remote", tags=["MCP-Remote"])


@router.get("/sse")
async def sse_endpoint(request: Request):
    """SSE connection endpoint — long-lived event stream for one MCP client.

    The client sends an ``Authorization: Bearer acw_<key>`` header. We
    validate it via our adapter (NOT the legacy middleware), set the
    ContextVar, then hand off to ``SseServerTransport.connect_sse``
    which holds the connection open and dispatches incoming messages.
    """
    try:
        key = await authenticate(request.headers.get("Authorization"))
    except MCPAuthError as exc:
        logger.warning("MCP SSE auth failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=status.HTTP_401_UNAUTHORIZED)

    token = _current_key.set(key)
    try:
        async with _sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await _mcp_server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="pfactory-remote",
                    server_version="1.0.0",
                    capabilities=_mcp_server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        _current_key.reset(token)


@router.post("/messages/")
async def messages_endpoint(request: Request):
    """Inbound JSON-RPC message endpoint for the SSE transport.

    SseServerTransport's ``handle_post_message`` does the body parsing
    and delivery into the active SSE session. We authenticate per-call
    here too because the SSE session's auth context is per-connection
    but message posts come on separate HTTP requests.
    """
    try:
        await authenticate(request.headers.get("Authorization"))
    except MCPAuthError as exc:
        logger.warning("MCP message-post auth failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return await _sse_transport.handle_post_message(request.scope, request.receive, request._send)
