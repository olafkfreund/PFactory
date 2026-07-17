"""Per-request tenant resolution (#308, factory-gitops#13).

Mirrors the CFactory pattern: multi-tenant mode is OFF by default, so tenant
resolution always yields the single ``"default"`` tenant and local single-user
behaviour is unchanged. When ``PFACTORY_MULTI_TENANT`` is truthy (hosted
deploy), the tenant is resolved per request from the ``X-Tenant-Id`` header the
ingress/oauth2-proxy stamps from the Keycloak ``tenant`` claim — falling back
to ``"default"`` when the header is absent or blank.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_TENANT = "default"
TENANT_HEADER = "X-Tenant-Id"


def multi_tenant_enabled() -> bool:
    """True when PFACTORY_MULTI_TENANT is set to a truthy value."""
    return os.environ.get("PFACTORY_MULTI_TENANT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_tenant(request: Any) -> str:
    """Resolve the request's tenant id (``"default"`` unless multi-tenant is on).

    ``request`` is anything with a ``.headers`` mapping (a FastAPI/Starlette
    ``Request``); ``None`` resolves to the default tenant, so non-HTTP callers
    (MCP, tests) need no stub.
    """
    if not multi_tenant_enabled() or request is None:
        return DEFAULT_TENANT
    value = (request.headers.get(TENANT_HEADER) or "").strip()
    return value or DEFAULT_TENANT
