"""Remote HTTP+SSE MCP server (Epic #50 / Issue #83).

Sister of the stdio MCP server at ``apps/backend/mcp_server/pfactory_server.py``.
Same control-plane surface (list/get/start/stop tasks), different transport
(HTTP+SSE instead of stdio) so non-Claude MCP clients — Cursor, Continue.dev,
custom scripts, Claude Code running on a different host making programmatic
calls — can observe and direct PFactory regardless of which LLM is doing the
work underneath.

Opt-in only. Set ``PFACTORY_MCP_REMOTE_ENABLED=true`` to mount; default off
to keep the v1.0 pilot's attack surface minimal.

Auth model
----------

The MCP server uses the existing ``acw_<urlsafe32>`` API keys from
``apps/web-server/server/routes/api_keys.py``. Two NEW scopes are required:

  * ``mcp:read``   — for list/get/diff/status tools
  * ``mcp:write``  — for start/stop/approve/reject/merge tools

The middleware-style ``TokenAuthMiddleware`` validates JWT + the legacy
``settings.API_TOKEN``; it does NOT validate ``acw_`` keys today. Per
issue #83 we add a thin auth adapter here that does ``acw_`` key
validation directly against the DB — same DB schema, no migration.

Hosting
-------

Embedded in the existing FastAPI app. Two routes:

  * ``GET  /api/mcp/sse``      — SSE event stream the client subscribes to
  * ``POST /api/mcp/messages/`` — client-to-server JSON-RPC frames

Both honour the ``Authorization: Bearer acw_<key>`` header.
"""

from __future__ import annotations

import os


def is_enabled() -> bool:
    """Return True iff the remote MCP server should be mounted on FastAPI.

    Mirrors the rmux toggle pattern (env-var driven, not a runtime setting)
    so an operator restart cleanly switches the surface on/off and
    `PFACTORY_MCP_REMOTE_ENABLED=true` is unambiguous in deployment manifests.
    """
    return os.environ.get("PFACTORY_MCP_REMOTE_ENABLED", "").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


__all__ = ["is_enabled"]
