"""
Authentication middleware for PFactory Web Server.

Supports dual authentication:
1. JWT tokens (primary) - validated via python-jose, populates request.state.user
2. Legacy bearer token (fallback) - simple string comparison for API key access

Public paths (no auth required): /api/auth/*, /api/health, static assets, etc.
"""

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import get_settings

logger = logging.getLogger(__name__)

# Bearer token security scheme for OpenAPI docs
bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Per-user API keys (acw_) for programmatic /api/* access (Issue #93)
# ---------------------------------------------------------------------------

# Prefix on every minted personal API token (see routes/api_keys.py).
API_KEY_PREFIX = "acw_"

# Scope that grants a key general access to the REST API surface (/api/*).
# Keeping this behind an explicit scope preserves the deliberate isolation of
# narrow MCP-only keys (mcp:read, task:write, …) — a key minted purely for the
# MCP control plane must NOT also unlock the full REST API unless its owner
# explicitly granted it the ``api`` scope. A single token may carry both
# ``api`` and ``mcp:*`` scopes to serve both surfaces.
API_SCOPE = "api"


def _hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest of a raw ``acw_`` key.

    Matches ``routes/api_keys.py::_hash_key``. Re-implemented here (rather than
    imported) to avoid an import cycle during middleware construction.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def _try_authenticate_api_key(token: str) -> dict | None:
    """Validate a personal ``acw_`` API key against the database.

    Returns a ``request.state.user`` dict (``{id, email, role}``) when the key
    exists, is unexpired, and carries the :data:`API_SCOPE`. Returns ``None``
    for anything else (unknown key, expired, or lacking the ``api`` scope) so
    the caller can fall through to the next strategy / reject.

    Side effect: bumps the key's ``last_used_at`` so the Settings UI can show
    when a token was last seen.
    """
    if not token.startswith(API_KEY_PREFIX):
        return None

    # Imported lazily: the database package pulls in SQLAlchemy models that
    # must not be imported at module-load time (circular-import risk during
    # app startup, mirroring mcp_remote/auth.py).
    from .database import ApiKey, User
    from .database.engine import get_db

    digest = _hash_api_key(token)
    # Stored form is ``<8-char-preview>$<sha256-hex>`` — match the part after $.
    async for session in get_db():
        result = await session.execute(
            select(ApiKey).where(ApiKey.key_hash.like(f"%${digest}"))
        )
        api_key = result.scalar_one_or_none()
        if api_key is None:
            return None

        # Reject expired keys.
        if api_key.expires_at is not None:
            expires_at = api_key.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                return None

        # Require the general ``api`` scope for REST access.
        scopes = {s.strip() for s in (api_key.scopes or "").split(",") if s.strip()}
        if API_SCOPE not in scopes:
            return None

        # Record last-used (best-effort — never block auth on a write failure).
        try:
            api_key.last_used_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception:  # noqa: BLE001 — last_used is non-critical telemetry
            await session.rollback()

        owner = await session.execute(select(User).where(User.id == api_key.user_id))
        user = owner.scalar_one_or_none()
        if user is None or not user.is_active:
            return None

        return {"id": user.id, "email": user.email, "role": user.role}

    return None


def _try_decode_jwt(token: str) -> dict | None:
    """Attempt to decode a JWT access token.

    Returns the decoded payload dict on success, or ``None`` if the token
    is not a valid JWT (e.g. it is a legacy API token).
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        # Only accept access tokens (not refresh tokens)
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to validate bearer token on API routes.

    Authentication strategy (tried in order):
    1. If the token is a valid JWT access token, populate
       ``request.state.user`` with the decoded claims and allow the request.
    2. If the token is a personal ``acw_`` API key carrying the ``api`` scope
       (Issue #93), resolve its owner, populate ``request.state.user``, and
       allow. Keys without the ``api`` scope (e.g. MCP-only keys) are rejected
       here so the REST surface stays scope-isolated.
    3. If the token matches the legacy ``settings.API_TOKEN``, set
       ``request.state.user`` to the default user (backward compatible) and allow.
    4. Otherwise reject with 401.
    """

    # Paths that don't require authentication
    PUBLIC_PATHS = {
        "/",
        "/index.html",
        "/favicon.ico",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/health",
        "/api/logs/frontend",  # Allow frontend error logs without auth
    }

    # Path prefixes that don't require authentication
    PUBLIC_PREFIXES = (
        "/assets/",
        "/static/",
        "/api/auth/",  # Auth endpoints (register, login, refresh, logout)
        "/api/email/auth/",  # OAuth callbacks (redirect from Microsoft/Google)
        # Remote MCP control plane (Epic #50 / Issue #83). The legacy
        # TokenAuthMiddleware validates JWT + the legacy API_TOKEN;
        # MCP clients send their ``acw_<key>`` instead, validated by
        # ``mcp_remote.auth.authenticate`` inside the route handler.
        # Adding the prefix here so the middleware doesn't 401 those
        # requests before our adapter runs.  Routes are only mounted
        # when PFACTORY_MCP_REMOTE_ENABLED=true, so this prefix is a
        # no-op on the default v1.0 pilot deployment.
        "/api/mcp-remote/",
        # Stdio MCP control-plane proxy (Issue #154). Same shape as
        # mcp-remote: the proxy handlers do their own ``acw_`` key
        # validation via ``mcp_stdio.auth.require_acw_scope``, so this
        # middleware must let those requests through without checking
        # JWT/legacy first.
        "/api/mcp-stdio/",
    )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Initialize user state to None for every request
        request.state.user = None

        # Skip all auth when disabled (development mode).
        # Populate request.state.user with the default user so that routes
        # using ``Depends(get_current_user)`` work too — the engine creates
        # the "default" user row on startup when DISABLE_AUTH is set.
        settings = get_settings()
        if settings.DISABLE_AUTH:
            request.state.user = {
                "id": "default",
                "email": "default@localhost",
                "role": "admin",
            }
            return await call_next(request)

        # Skip auth for public paths
        if path in self.PUBLIC_PATHS:
            return await call_next(request)

        # Skip auth for public prefixes (static files + auth routes)
        if path.startswith(self.PUBLIC_PREFIXES):
            return await call_next(request)

        # Skip auth for non-API paths (SPA routes handled by static files)
        if not path.startswith("/api"):
            return await call_next(request)

        # --- Authenticate API routes ---
        # Accept the token from the Authorization: Bearer header, OR fall back
        # to the `access_token` cookie set by the OIDC login callback (HttpOnly
        # cookie session). Without this, SSO logins set a cookie the middleware
        # ignored, so the SPA bounced straight back to /login.
        settings = get_settings()
        auth_header = request.headers.get("Authorization")
        token = None
        if auth_header:
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    {"error": "Invalid Authorization header format"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            token = auth_header[7:]  # Remove "Bearer " prefix
        else:
            token = request.cookies.get("access_token")

        if not token:
            return JSONResponse(
                {"error": "Missing Authorization header"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        # Strategy 1: Try JWT validation
        jwt_payload = _try_decode_jwt(token)
        if jwt_payload is not None:
            # Populate request.state.user with JWT claims
            request.state.user = {
                "id": jwt_payload.get("sub"),
                "email": jwt_payload.get("email"),
                "role": jwt_payload.get("role", "user"),
            }
            return await call_next(request)

        # Strategy 2: Personal API key (acw_) with the ``api`` scope (Issue #93).
        # Lets a logged-in user mint a GitHub-PAT-style token from Settings and
        # use it as ``Authorization: Bearer acw_…`` against /api/* (and the MCP
        # server + /handover), retiring the shared APP_API_TOKEN stopgap.
        if token.startswith(API_KEY_PREFIX):
            api_user = await _try_authenticate_api_key(token)
            if api_user is not None:
                request.state.user = api_user
                return await call_next(request)
            return JSONResponse(
                {"error": "Invalid or insufficiently scoped API key"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        # Strategy 3: Fall back to legacy bearer token
        if token == settings.API_TOKEN:
            # Legacy token — populate a default user so notifications still work
            request.state.user = {
                "id": "default",
                "email": None,
                "role": "user",
            }
            return await call_next(request)

        # Neither JWT nor legacy token matched
        return JSONResponse(
            {"error": "Invalid token"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = None,
) -> str:
    """Dependency to verify bearer token in route handlers.

    Accepts both JWT tokens and legacy API tokens.
    """
    settings = get_settings()

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Accept valid JWT access tokens
    if _try_decode_jwt(token) is not None:
        return token

    # Accept legacy API token
    if token == settings.API_TOKEN:
        return token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def verify_websocket_token(websocket: WebSocket) -> bool:
    """Verify token for WebSocket connections.

    Token can be passed as:
    - Query parameter: ?token=xxx
    - Header: Authorization: Bearer xxx

    Accepts both JWT tokens and legacy API tokens.
    """
    settings = get_settings()

    if settings.DISABLE_AUTH:
        return True

    # Try query parameter first
    token = websocket.query_params.get("token")

    # Fall back to header
    if not token:
        auth_header = websocket.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        await websocket.close(code=4001, reason="Unauthorized")
        return False

    # Accept valid JWT access tokens
    if _try_decode_jwt(token) is not None:
        return True

    # Accept legacy API token
    if token == settings.API_TOKEN:
        return True

    await websocket.close(code=4001, reason="Unauthorized")
    return False


async def authenticate_websocket(websocket: WebSocket) -> dict | None:
    """Authenticate a WebSocket connection and return user info.

    Returns a dict with user claims on success, or ``None`` for legacy
    token connections.  Closes the socket and raises if authentication
    fails entirely.

    The returned dict (when not None) contains:
    - ``id``: user UUID
    - ``email``: user email
    - ``role``: global role
    """
    settings = get_settings()

    if settings.DISABLE_AUTH:
        return None

    # Try query parameter first
    token = websocket.query_params.get("token")

    # Fall back to header
    if not token:
        auth_header = websocket.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        await websocket.close(code=4001, reason="Unauthorized")
        raise WebSocketAuthError("No token provided")

    # Try JWT — returns user info
    jwt_payload = _try_decode_jwt(token)
    if jwt_payload is not None:
        return {
            "id": jwt_payload.get("sub"),
            "email": jwt_payload.get("email"),
            "role": jwt_payload.get("role", "user"),
        }

    # Fall back to legacy token — no user info available
    if token == settings.API_TOKEN:
        return None

    await websocket.close(code=4001, reason="Unauthorized")
    raise WebSocketAuthError("Invalid token")


class WebSocketAuthError(Exception):
    """Raised when WebSocket authentication fails."""

    pass
