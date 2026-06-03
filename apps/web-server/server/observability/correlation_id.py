"""Correlation ID propagation (Epic #26 P6.2).

X-Request-ID is the de-facto header for HTTP-tier request tracing.
This middleware:
  - Reads X-Request-ID from the incoming request; auto-generates a
    UUID if absent.
  - Stashes the value in a contextvar (read by structlog's
    _add_request_id processor + by outbound httpx via the
    install_httpx_propagation hook).
  - Echoes the ID back in the response so the caller can correlate
    their request to our log lines.

The contextvar pattern means correlation IDs propagate through
``await`` calls within the request without explicit threading.
"""

from __future__ import annotations

import contextvars
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

CORRELATION_ID_HEADER = "X-Request-ID"

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pfactory.request_id", default=None
)


def get_correlation_id() -> str | None:
    """Return the current request's ID, or None outside a request scope."""
    return _correlation_id.get()


def set_correlation_id(rid: str | None) -> contextvars.Token:
    """Test / library hook to set the contextvar; returns the reset token."""
    return _correlation_id.set(rid)


def reset_correlation_id(token: contextvars.Token) -> None:
    _correlation_id.reset(token)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that ensures every request carries a correlation ID.

    Order matters: install BEFORE the auth middleware so even
    401-rejected requests carry the ID in their response (auditors
    rely on this to trace failed auth attempts).
    """

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        token = _correlation_id.set(rid)
        try:
            response = await call_next(request)
        finally:
            _correlation_id.reset(token)
        response.headers[CORRELATION_ID_HEADER] = rid
        return response


def install_httpx_propagation() -> None:
    """Patch httpx clients to forward the current correlation ID.

    Idempotent. Applies to BOTH httpx.Client and httpx.AsyncClient at
    module level — every client instance created after this call
    propagates the contextvar's X-Request-ID on outgoing requests.

    The wrapping uses an event hook so application code doesn't need
    to know correlation IDs exist.
    """
    import httpx

    if getattr(httpx, "_pfactory_corr_id_installed", False):
        return

    def _add_correlation_header(request):
        rid = get_correlation_id()
        if rid and CORRELATION_ID_HEADER not in request.headers:
            request.headers[CORRELATION_ID_HEADER] = rid

    # Wrap Client and AsyncClient __init__ to append the hook.
    _orig_client_init = httpx.Client.__init__
    _orig_aclient_init = httpx.AsyncClient.__init__

    def _client_init(self, *args, **kwargs):
        event_hooks = kwargs.setdefault("event_hooks", {})
        req_hooks = list(event_hooks.get("request") or [])
        req_hooks.append(_add_correlation_header)
        event_hooks["request"] = req_hooks
        _orig_client_init(self, *args, **kwargs)

    def _aclient_init(self, *args, **kwargs):
        event_hooks = kwargs.setdefault("event_hooks", {})
        req_hooks = list(event_hooks.get("request") or [])
        # AsyncClient hooks must be coroutines.
        async def _ahook(request):
            _add_correlation_header(request)
        req_hooks.append(_ahook)
        event_hooks["request"] = req_hooks
        _orig_aclient_init(self, *args, **kwargs)

    httpx.Client.__init__ = _client_init
    httpx.AsyncClient.__init__ = _aclient_init
    httpx._pfactory_corr_id_installed = True
