"""Capability discovery endpoint.

The frontend hits this once on mount to know which optional features
are turned on for this server, so it can render the right UI without
404-probing every endpoint.  Added in Epic #44 R2 specifically so the
Live Console tab can hide itself when ``PFACTORY_RMUX_ENABLED`` is
unset — but the endpoint is intentionally extensible (it just returns
a flat dict the frontend can consult).

Always mounted.  Returning an extra boolean costs nothing; teaching
the bank-pilot frontend to politely 404 because rmux is off would be
worse.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..rmux.integration import is_enabled as is_rmux_enabled

router = APIRouter(prefix="/api/capabilities", tags=["Capabilities"])


@router.get("")
async def get_capabilities() -> dict:
    """Return the set of optional features enabled on this server.

    Response shape (additive — frontend should treat unknown keys as
    "feature not supported"):

      {
        "rmux": bool,           # Epic #44 Live Agent Console
      }
    """
    return {
        "rmux": is_rmux_enabled(),
    }
