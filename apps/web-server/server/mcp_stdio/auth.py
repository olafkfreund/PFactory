"""Auth + scope gating for the stdio-MCP proxy (Issue #154).

Routes under ``/api/mcp-stdio/`` accept two kinds of bearer tokens:

1. **acw_ API keys** minted via ``routes/api_keys.py`` — validated
   against the ``ApiKey`` table, hashed at rest, scope-gated. This is
   the recommended path for enterprise installs: each developer holds
   a scoped key, no shared host-wide secret.

2. **Legacy admin token** (``settings.API_TOKEN`` — usually backed by
   ``~/.pfactory/.token``) — acts as a wildcard "all scopes" key.
   This preserves v1.0 behavior so single-user laptops keep working
   without minting a key.

Scopes are the same vocabulary used by ``mcp_remote`` plus a few
finer-grained ones for write operations:

- ``mcp:read``     — read tools (list/status/logs)
- ``project:write`` — create projects
- ``task:write``    — start, stop, recover, approve, create-and-run tasks
- ``task:merge``    — create-pr, merge-pr (default-branch blast radius)

Use ``require_acw_scope(scope)`` as a FastAPI dependency on each
proxy route. It returns the resolved ``AuthenticatedKey`` (or a
synthetic legacy key) so handlers can record key_id in audit logs.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from ..config import get_settings
from ..mcp_remote import auth as mcp_remote_auth

# Re-export the named scopes so callers don't have to know which
# module owns the constants.
MCP_READ_SCOPE = "mcp:read"
PROJECT_WRITE_SCOPE = "project:write"
TASK_WRITE_SCOPE = "task:write"
TASK_MERGE_SCOPE = "task:merge"

ALL_SCOPES = frozenset({
    MCP_READ_SCOPE,
    PROJECT_WRITE_SCOPE,
    TASK_WRITE_SCOPE,
    TASK_MERGE_SCOPE,
})


@dataclass(frozen=True)
class _LegacyAdminKey:
    """Synthetic key returned when the caller authenticated via the
    legacy admin token. Behaves like a real ``AuthenticatedKey`` but
    advertises every scope so existing v1.0 callers keep working.

    Mirroring the surface (``key_id``, ``user_id``, ``has_scope``) lets
    handlers treat both auth paths uniformly. ``key_id="legacy-admin"``
    in audit logs makes it easy to spot calls that bypassed scopes.
    """

    key_id: str = "legacy-admin"
    user_id: str | None = None

    @property
    def scopes(self) -> frozenset[str]:
        return ALL_SCOPES

    def has_scope(self, _scope: str) -> bool:
        return True


def _strip_bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization[7:].strip()


def require_acw_scope(scope: str):
    """FastAPI ``Depends`` factory — returns a coroutine that resolves
    the auth header into either an ``AuthenticatedKey`` (acw_ path) or
    a ``_LegacyAdminKey`` (legacy admin path), and rejects when the
    resolved key lacks ``scope``.

    HTTP status mapping:
    - Missing / malformed Authorization → 401
    - Unknown / disabled acw_ key       → 401
    - Authenticated but lacks scope      → 403

    The 401-vs-403 split matters for the stdio MCP client's error
    messages: 401 says "your key is bad" (regenerate); 403 says "your
    key works but is scoped wrong" (mint a new one with the right
    scope).
    """

    async def _check(request: Request):
        settings = get_settings()
        token = _strip_bearer(request.headers.get("Authorization"))
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or malformed Authorization header (expected 'Bearer <token>')",
            )

        # Legacy admin token = wildcard. Compare against settings.API_TOKEN.
        if token == settings.API_TOKEN:
            return _LegacyAdminKey()

        try:
            key = await mcp_remote_auth.authenticate(f"Bearer {token}")
        except mcp_remote_auth.MCPAuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc

        if not key.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"API key lacks required scope '{scope}'. "
                    f"Mint a new key with the right scope via Settings → API Keys."
                ),
            )

        return key

    return _check
