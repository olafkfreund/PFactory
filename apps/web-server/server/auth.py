"""
Authentication middleware for Magestic AI Web Server.

Supports dual authentication:
1. JWT tokens (primary) - validated via python-jose, populates request.state.user
2. Legacy bearer token (fallback) - simple string comparison for API key access

Public paths (no auth required): /api/auth/*, /api/health, static assets, etc.
"""

import logging

from fastapi import HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import get_settings

logger = logging.getLogger(__name__)

# Bearer token security scheme for OpenAPI docs
bearer_scheme = HTTPBearer(auto_error=False)


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
    2. If the token matches the legacy ``settings.API_TOKEN``, set
       ``request.state.user = None`` (backward compatible) and allow.
    3. Otherwise reject with 401.
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
        settings = get_settings()
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return JSONResponse(
                {"error": "Missing Authorization header"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "Invalid Authorization header format"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        token = auth_header[7:]  # Remove "Bearer " prefix

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

        # Strategy 2: Fall back to legacy bearer token
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
