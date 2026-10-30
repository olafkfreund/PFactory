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
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from mcp.server.lowlevel import Server
from mcp.server.sse import SseServerTransport
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from .auth import MCPAuthError, authenticate
from .tools import dispatch_tool_call, get_tool_definitions

_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from client_errors import client_error  # noqa: E402  (after sys.path insert)

logger = logging.getLogger(__name__)


# ContextVar for the current request's authenticated key. The tool
# handler reads this — we can't pass it through the MCP SDK's call
# signature.
import contextvars

_current_key: contextvars.ContextVar = contextvars.ContextVar(
    "mcp_remote_current_key", default=None
)


async def _list_tools(_ctx: Any, _params: Any) -> ListToolsResult:
    """Return the static tool catalog. No auth check — discovery is open."""
    return ListToolsResult(
        tools=[
            Tool(
                name=d["name"],
                description=d["description"],
                inputSchema=d["inputSchema"],
            )
            for d in get_tool_definitions()
        ]
    )


async def _call_tool(_ctx: Any, params: CallToolRequestParams) -> CallToolResult:
    """Dispatch a tool call — auth + scope check + result envelope."""
    key = _current_key.get()
    if key is None:
        # Shouldn't happen — the route handler sets it before delegating.
        # If it does, treat as unauthenticated so we never accidentally
        # serve a tool without a key.
        return CallToolResult(
            content=[TextContent(type="text", text="Error: missing auth context")],
            is_error=True,
        )
    result = await dispatch_tool_call(params.name, params.arguments, key)
    return CallToolResult(
        content=[
            TextContent(type="text", text=b.get("text", "")) for b in result.get("content", [])
        ],
        is_error=bool(result.get("isError")),
    )


# mcp 2.x lowlevel API: handlers are passed to the constructor (#946).
_mcp_server: Server = Server(
    "pfactory-remote",
    version="1.0.0",
    on_list_tools=_list_tools,
    on_call_tool=_call_tool,
)


# SSE transport instance — endpoint is the path where the client posts
# messages back to. Matches the path the messages route handles.
# Note: ``/api/mcp/`` is already used by ``routes/git.py::mcp_router``
# for the MCP-Git-provider feature; we use ``/api/mcp-remote/`` to keep
# the two surfaces independent.
_sse_transport = SseServerTransport("/api/mcp-remote/messages/")

# SSE session id -> id of the key that opened it, so a message POST must come
# from the same key the session dispatches as (#946). The transport generates
# the id internally; we read it from the ``endpoint`` event it sends.
_session_keys: dict[str, str] = {}
_ENDPOINT_EVENT = re.compile(
    rb"event: endpoint\r?\ndata: [^\r\n]*[?&]session_id=([0-9a-f]{32})\r?\n"
)


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
        # The specific reason (bad key, missing scope, expired) stays in the log:
        # an unauthenticated caller should not be able to enumerate what exists
        # by reading back which part of the check failed.
        logger.warning("MCP SSE auth failed: %s", exc)
        return JSONResponse(
            {"error": "authentication failed"}, status_code=status.HTTP_401_UNAUTHORIZED
        )

    session_ids: list[str] = []
    first_body = [True]

    async def send(message: Any) -> None:
        # Only the first body is the transport's own endpoint event; anything
        # later is tool output and must never (re)bind a session.
        if first_body[0] and message.get("type") == "http.response.body":
            first_body[0] = False
            match = _ENDPOINT_EVENT.match(message.get("body", b""))
            if match:
                session_ids.append(match.group(1).decode())
                _session_keys[session_ids[0]] = key.key_id
        await request._send(message)

    token = _current_key.set(key)
    try:
        async with _sse_transport.connect_sse(request.scope, request.receive, send) as (
            read_stream,
            write_stream,
        ):
            await _mcp_server.run(
                read_stream,
                write_stream,
                _mcp_server.create_initialization_options(),
            )
    finally:
        _current_key.reset(token)
        for session_id in session_ids:
            _session_keys.pop(session_id, None)
    # The transport already sent the whole SSE response.
    return Response()


@router.post("/messages/")
async def messages_endpoint(request: Request):
    """Inbound JSON-RPC message endpoint for the SSE transport.

    SseServerTransport's ``handle_post_message`` does the body parsing
    and delivery into the active SSE session. We authenticate per-call
    here too because the SSE session's auth context is per-connection
    but message posts come on separate HTTP requests.
    """
    try:
        key = await authenticate(request.headers.get("Authorization"))
    except MCPAuthError as exc:
        logger.warning("MCP message-post auth failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=client_error(exc)
        ) from exc
    # Tools run as the key captured at SSE connect, so only that key may post.
    session_id = request.query_params.get("session_id", "")
    if _session_keys.get(session_id) != key.key_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This MCP session belongs to another API key",
        )
    # mcp 2.x hands each message the POSTer's contextvars, not the SSE task's,
    # so the tool handler reads the key from here; it equals the session owner.
    token = _current_key.set(key)
    try:
        return await _sse_transport.handle_post_message(
            request.scope, request.receive, request._send
        )
    finally:
        _current_key.reset(token)
