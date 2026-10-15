"""server: the MCP tools listed to clients match the handlers that serve them.

``MCP_TOOLS`` (tools/list) and ``_TOOL_HANDLERS`` (tools/call) are separate. A
listed tool with no handler raises ``KeyError`` at call time; a handler that is
never listed cannot be reached by a client.
"""

# ruff: noqa: PLC0415

from __future__ import annotations

from collections.abc import Iterator

from factory_invariants import registry


def _mcp_tools_have_handlers() -> Iterator[str]:
    from server.routes import mcp_rpc

    listed = {t["name"] for t in mcp_rpc.MCP_TOOLS}
    handled = set(mcp_rpc._TOOL_HANDLERS)
    for name in sorted(listed - handled):
        yield f"MCP tool {name!r} is listed but has no handler (KeyError on call)"
    for name in sorted(handled - listed):
        yield f"MCP handler {name!r} exists but the tool is never listed"


registry.register("server.routes.mcp_rpc", _mcp_tools_have_handlers)
