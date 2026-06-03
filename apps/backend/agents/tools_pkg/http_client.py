"""HTTP client used by the standalone MCP server's task-control tools.

Sole purpose: let MCP tools (running as a stdio subprocess of the user's
Claude Code session) call into the PFactory web-server's REST API to
list/inspect/drive tasks.

Why a separate client (not just ``httpx.AsyncClient`` inline):
- One place for the bearer-token chain (env override → ``~/.pfactory/.token``)
  so token rotation works without restarting the MCP subprocess.
- One place for the friendly-error mapping so every tool returns the
  same operator guidance when the web-server is down, the token is
  rejected, or the server returns 5xx.
- Lazy-initialized client so a bare ``--help`` on the server doesn't
  open a connection pool.

Per Issue #154 (v1.1 RBAC): tools now talk to the scope-gated proxy
under ``/api/mcp-stdio/*`` and prefer the per-user ``acw_`` key from
``PFACTORY_MCP_KEY`` (or ``~/.pfactory/.mcp-key``) over the legacy
admin token at ``~/.pfactory/.token``. The legacy token continues to
work as a wildcard fallback so v1.0 single-user laptops keep working.

Token resolution order (first non-empty wins):
1. ``$PFACTORY_MCP_KEY``                  — env, recommended for enterprise
2. ``~/.pfactory/.mcp-key``               — file form of #1
3. ``$PFACTORY_API_TOKEN_FILE`` (legacy)  — legacy override
4. ``~/.pfactory/.token``                 — legacy admin token (wildcard)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]


DEFAULT_API_URL = "http://localhost:3102"
DEFAULT_TOKEN_FILE = "~/.pfactory/.token"
DEFAULT_MCP_KEY_FILE = "~/.pfactory/.mcp-key"
DEFAULT_TIMEOUT = 30.0
# All stdio-MCP requests go through the scope-gated proxy mounted at
# this prefix (Issue #154). The proxy delegates to the same service
# the regular ``/api/`` routes use, so payload shapes are identical.
MCP_PROXY_PREFIX = "/api/mcp-stdio"


class MCPHTTPError(RuntimeError):
    """Operator-actionable error from the MCP HTTP client.

    The string form is what the tool surfaces in its ``content[0].text``
    response — keep it single-line, no stack traces, and end with a
    concrete next step.
    """


class _ClientState:
    """Lazy singleton — opened on first request, reused thereafter."""

    def __init__(self) -> None:
        self._client: Any = None  # httpx.AsyncClient | None
        self._base_url: str | None = None

    def base_url(self) -> str:
        # Re-evaluated each call so an operator can change PFACTORY_API_URL
        # in a running shell without restarting the MCP subprocess.
        return os.environ.get("PFACTORY_API_URL", DEFAULT_API_URL).rstrip("/")

    async def get_client(self) -> Any:
        if not HTTPX_AVAILABLE:
            raise MCPHTTPError(
                "httpx not installed in the MCP subprocess venv — "
                "install it with: pip install httpx"
            )
        base = self.base_url()
        if self._client is None or self._base_url != base:
            if self._client is not None:
                await self._client.aclose()
            self._client = httpx.AsyncClient(base_url=base, timeout=DEFAULT_TIMEOUT)
            self._base_url = base
        return self._client


_state = _ClientState()


def _read_token() -> str:
    """Return the bearer token used to call the stdio-MCP proxy.

    Re-read at every call so operators can rotate the token (regenerate via
    the web UI, write the file, no restart needed) without redeploying the
    MCP subprocess. Never echo this value in error messages.

    Resolution order — first non-empty source wins:
    1. ``$PFACTORY_MCP_KEY`` env (recommended for enterprise: scope-gated)
    2. ``~/.pfactory/.mcp-key`` file (file form of #1)
    3. ``$PFACTORY_API_TOKEN_FILE`` (legacy override path)
    4. ``~/.pfactory/.token`` (legacy admin token — wildcard scopes)
    """
    # 1. Env var — highest precedence so a developer can scope a single
    # shell to a different key without touching the file.
    env_key = os.environ.get("PFACTORY_MCP_KEY", "").strip()
    if env_key:
        return env_key

    # 2. Per-user scoped key file. Same shape as the legacy token file
    # but holds an ``acw_`` key instead of the admin token.
    mcp_key_path = Path(DEFAULT_MCP_KEY_FILE).expanduser()
    if mcp_key_path.exists():
        try:
            token = mcp_key_path.read_text().strip()
        except OSError as exc:
            raise MCPHTTPError(
                f"Cannot read PFactory MCP key at {mcp_key_path}: {exc}"
            ) from exc
        if token:
            return token

    # 3-4. Legacy admin token fallback.
    token_path = Path(
        os.environ.get("PFACTORY_API_TOKEN_FILE", DEFAULT_TOKEN_FILE)
    ).expanduser()
    if not token_path.exists():
        raise MCPHTTPError(
            f"PFactory MCP key not found — set $PFACTORY_MCP_KEY, write "
            f"{mcp_key_path}, or regenerate the legacy token at {token_path} "
            "via the web UI."
        )
    try:
        token = token_path.read_text().strip()
    except OSError as exc:
        raise MCPHTTPError(
            f"Cannot read PFactory token at {token_path}: {exc}"
        ) from exc
    if not token:
        raise MCPHTTPError(
            f"PFactory token at {token_path} is empty — regenerate via the web UI"
        )
    return token


async def request(method: str, path: str, **kwargs: Any) -> dict[str, Any] | list:
    """Make an authenticated request against the PFactory web-server.

    ``kwargs`` are forwarded to ``httpx.AsyncClient.request`` (e.g.
    ``params=``, ``json=``). The bearer token is added to ``headers``;
    any caller-supplied ``Authorization`` header is overridden — single
    auth path for the MCP control plane.

    Returns the parsed JSON body on success. Raises ``MCPHTTPError`` with
    operator-actionable single-line guidance on failure:
    - Connection refused → "web-server not reachable, start with: ..."
    - 401 → "token rejected, regenerate via web UI"
    - 5xx → "server error: <truncated body>"
    """
    if not HTTPX_AVAILABLE:
        raise MCPHTTPError(
            "httpx not installed in the MCP subprocess venv — "
            "install it with: pip install httpx"
        )

    token = _read_token()
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {token}"

    # Rewrite ``/api/...`` → ``/api/mcp-stdio/...`` so every stdio MCP call
    # hits the scope-gated proxy. Paths already under the proxy prefix
    # pass through unchanged (allows future direct callers / tests).
    if path.startswith("/api/") and not path.startswith(f"{MCP_PROXY_PREFIX}/"):
        path = MCP_PROXY_PREFIX + path[len("/api"):]

    client = await _state.get_client()
    base = _state.base_url()

    try:
        response = await client.request(method, path, headers=headers, **kwargs)
    except httpx.ConnectError as exc:
        raise MCPHTTPError(
            f"PFactory web-server not reachable at {base} — "
            "start it with: python -m server.main"
        ) from exc
    except httpx.TimeoutException as exc:
        raise MCPHTTPError(
            f"PFactory web-server at {base} timed out after {DEFAULT_TIMEOUT}s"
        ) from exc

    if response.status_code == 401:
        raise MCPHTTPError(
            "PFactory MCP key rejected — mint a fresh key in Settings → "
            "API Keys (or check $PFACTORY_MCP_KEY / ~/.pfactory/.mcp-key)."
        )
    if response.status_code == 403:
        # The proxy includes the missing scope name in the body. Pull it
        # out so the user knows exactly which key to mint.
        body = response.text[:300]
        raise MCPHTTPError(
            f"PFactory MCP key lacks the required scope: {body} — "
            "mint a new key with the right scope in Settings → API Keys."
        )
    if response.status_code == 404:
        # Tools may want to differentiate "no such resource" from other
        # errors; surface a structured message but stay a single line.
        raise MCPHTTPError(
            f"Resource not found at {method} {path} (HTTP 404)"
        )
    if response.status_code >= 500:
        body = response.text[:500]
        raise MCPHTTPError(
            f"PFactory web-server returned HTTP {response.status_code}: {body}"
        )
    if response.status_code >= 400:
        body = response.text[:500]
        raise MCPHTTPError(
            f"PFactory web-server returned HTTP {response.status_code}: {body}"
        )

    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise MCPHTTPError(
            f"PFactory web-server returned non-JSON body: {response.text[:200]}"
        ) from exc


async def reset() -> None:
    """Close the underlying client. Test/CLI helper, not used at runtime."""
    if _state._client is not None:
        await _state._client.aclose()
        _state._client = None
        _state._base_url = None
