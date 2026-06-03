"""Stdio-MCP control-plane (Issue #154).

The stdio MCP server (run as a subprocess by Claude Code on developer
laptops) used to call into the regular REST surface using the legacy
admin token at ``~/.pfactory/.token``. That meant any process that
could read the token file got full host-wide admin power — fine for
single-user dev, not OK for enterprise hosts.

This module exposes a small **proxy** API under ``/api/mcp-stdio/*``
that re-exposes only the 15 operations the stdio MCP exercises. Each
proxy route is gated by an ``acw_<key>`` API key with a scoped
permission (``mcp:read`` / ``project:write`` / ``task:write`` /
``task:merge``). The legacy admin token at ``~/.pfactory/.token``
continues to work as a wildcard so v1.0 deployments are unaffected.

Why a proxy instead of extending ``TokenAuthMiddleware``:
- ``mcp_remote/auth.py`` already argued (with reason) against letting
  ``acw_`` keys onto the regular REST surface. A user with ``mcp:read``
  shouldn't be able to GET ``/api/tasks`` on the JWT-protected REST
  routes — only on this proxy that intends to expose it.
- Keeps the v1.0 REST middleware untouched. No risk of forgetting a
  scope gate on a new ``/api/`` route silently widening ``acw_`` power.
"""

from .router import router

__all__ = ["router"]
