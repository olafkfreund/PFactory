"""Federated cross-portal search proxy (#149).

The cockpit (CFactory) is the fleet aggregator: it ingests every portal's work
and exposes a ranked ``/api/search`` across all four. This route lets THIS
portal's UI offer that same global search same-origin — the browser calls our
``/api/search`` (already authenticated to this portal) and we forward the query
server-side to the cockpit with a read-scoped cockpit key. Keeping the cockpit
credential server-side means the browser never makes a cross-origin,
cross-credential call.

Failures degrade to an empty result set so the command palette stays usable when
the cockpit is unreachable or the feature is unconfigured.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query

from server.config import get_settings
from server.routes.auth_routes import get_current_user

router = APIRouter()


@router.get("/api/search")
async def federated_search(
    _user: Annotated[object, Depends(get_current_user)],
    q: Annotated[str, Query(description="Search query")] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    settings = get_settings()
    base = (settings.CFACTORY_SEARCH_URL or "").rstrip("/")
    if not q.strip() or not base:
        return {"query": q, "count": 0, "results": []}

    headers = {}
    if settings.CFACTORY_READ_KEY:
        headers["Authorization"] = f"Bearer {settings.CFACTORY_READ_KEY}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{base}/api/search",
                params={"q": q, "limit": limit},
                headers=headers,
            )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return {"query": q, "count": 0, "results": []}
