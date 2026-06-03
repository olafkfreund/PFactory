"""Auth adapter for the Remote HTTP+SSE MCP server.

``TokenAuthMiddleware`` (the web-server's main bearer-token gate) validates
JWTs + the legacy ``settings.API_TOKEN``. It does NOT validate the
``acw_<urlsafe32>`` API keys minted via ``routes/api_keys.py``.

Per issue #83's design, the MCP control plane uses ``acw_`` keys — not the
admin-grade legacy token — so it can be scope-gated (``mcp:read`` /
``mcp:write``). This module bridges that gap with a thin DB-backed
validator.

Why not extend ``TokenAuthMiddleware`` instead?
- The legacy middleware sees every API route, including non-MCP ones. We
  don't want a key with only ``mcp:read`` to be able to GET ``/api/tasks``
  on the regular REST surface — that's the legacy bearer's job.
- Keeping the scope check local to the MCP module means the regular REST
  middleware stays exactly as-is; nothing about the v1.0 pilot's auth
  posture changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.engine import get_db
from ..database.models import ApiKey

MCP_READ_SCOPE = "mcp:read"
MCP_WRITE_SCOPE = "mcp:write"


@dataclass(frozen=True)
class AuthenticatedKey:
    """Result of a successful ``acw_`` key validation."""

    key_id: str
    """API-key row id — recorded in audit logs."""

    scopes: frozenset[str]
    """Scopes attached to this key, parsed from the comma-separated DB column."""

    user_id: str | None
    """Resolved user id for audit attribution. May be None for legacy keys."""

    org_id: str | None = None
    """Resolved org id from the ApiKey row. Used to scope audit log
    entries to the right organization (Epic #50 acceptance criterion #2)."""

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


class MCPAuthError(Exception):
    """Raised on auth failures; the SSE/JSON-RPC layer maps to HTTP 401/403."""


def _hash_key(raw_key: str) -> str:
    """Same SHA-256 hex digest as ``routes/api_keys.py::_hash_key``.

    Re-implemented here (rather than imported) to avoid a circular-import
    risk when the MCP module mounts during ``main.py`` startup before
    ``routes/api_keys.py`` is fully imported. The hash is a 2-line
    function and unlikely to drift.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _strip_bearer(authorization: str | None) -> str | None:
    """Extract the raw token from ``Authorization: Bearer <token>``."""
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None
    return authorization[7:].strip()


async def authenticate(authorization_header: str | None) -> AuthenticatedKey:
    """Validate an ``acw_`` API key from the request header.

    Raises ``MCPAuthError`` with a single-line message on:
    - Missing ``Authorization`` header
    - Non-Bearer scheme
    - Unknown key (hash doesn't match any row)
    - Disabled key (``is_active=False``)

    Scope enforcement is the CALLER's job — this function only proves
    the key exists and is enabled. Use ``require_scope()`` after to
    gate specific tools.
    """
    raw = _strip_bearer(authorization_header)
    if not raw:
        raise MCPAuthError("Missing or malformed Authorization header (expected 'Bearer <token>')")

    digest = _hash_key(raw)

    async for session in get_db():
        return await _lookup_by_digest(session, digest)
    # get_db yields exactly once; if we got here, the dependency didn't
    # run — surface as an auth error rather than a silent pass.
    raise MCPAuthError("Database session not available")


async def _lookup_by_digest(session: AsyncSession, digest: str) -> AuthenticatedKey:
    """DB query helper — separated for unit-test stubbing."""
    # ApiKey stores hashes as ``<8-char-preview>$<sha256-hex>`` (see
    # routes/api_keys.py::_store_key_hash). The digest we want to match
    # against is the part AFTER the ``$`` separator.
    stmt = select(ApiKey).where(ApiKey.key_hash.like(f"%${digest}"))
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise MCPAuthError("Invalid API key")
    if not row.is_active:
        raise MCPAuthError("API key has been revoked")

    scopes_raw = row.scopes or ""
    scopes = frozenset(s.strip() for s in scopes_raw.split(",") if s.strip())
    return AuthenticatedKey(
        key_id=str(row.id),
        scopes=scopes,
        user_id=str(row.user_id) if row.user_id else None,
        org_id=str(row.org_id) if row.org_id else None,
    )


def require_scope(key: AuthenticatedKey, scope: str) -> None:
    """Raise ``MCPAuthError`` unless the key has the named scope.

    Use this at the top of every tool handler:

        async def list_tasks(args):
            require_scope(current_key.get(), MCP_READ_SCOPE)
            ...
    """
    if not key.has_scope(scope):
        raise MCPAuthError(
            f"API key lacks required scope '{scope}'. "
            f"Mint a new key with the right scope via the web UI."
        )
