"""Auto-Fix REST routes — backs the ``useAutoFix`` hook on the frontend.

The frontend currently calls ``window.API.github.*`` (the old Electron
IPC shim); the api-adapter at ``apps/frontend-web/src/lib/api-adapter.ts``
is being rewritten to call THESE endpoints instead.

Contract is defined by the AutoFixConfig + AutoFixQueueItem types at
``apps/frontend-web/src/shared/types/github-api.ts``.

Endpoints:
  - GET  /api/projects/{projectId}/auto-fix/config       — load config
  - PUT  /api/projects/{projectId}/auto-fix/config       — save config
  - GET  /api/projects/{projectId}/auto-fix/queue        — list queue items
  - POST /api/projects/{projectId}/auto-fix/check-new    — manual poll
  - POST /api/projects/{projectId}/auto-fix/{N}/start    — single-issue start

Returns are RAW dicts (not wrapped in ``{success, data}``) — the
frontend's api-adapter expects the schema directly per the existing
hook in ``useAutoFix.ts``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import auto_fix_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class AutoFixConfigPayload(BaseModel):
    """Mirrors AutoFixConfig at apps/frontend-web/src/shared/types/github-api.ts:21"""
    enabled: bool = False
    labels: list[str] = Field(default_factory=list)
    requireHumanApproval: bool = False
    botToken: str | None = ""
    model: str = "sonnet"
    thinkingLevel: str = "none"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{projectId}/auto-fix/config")
async def get_auto_fix_config(projectId: str) -> dict[str, Any] | None:
    """Return AutoFixConfig for the project, or null if no project."""
    cfg = auto_fix_service.get_config(projectId)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Project not found")
    # Strip the queue from the config response (queue has its own endpoint)
    return {k: v for k, v in cfg.items() if k != "queue"}


@router.put("/{projectId}/auto-fix/config")
async def save_auto_fix_config(projectId: str, payload: AutoFixConfigPayload) -> dict[str, bool]:
    """Save AutoFixConfig. Returns ``{success: bool}``."""
    ok = auto_fix_service.save_config(projectId, payload.model_dump())
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True}


@router.get("/{projectId}/auto-fix/queue")
async def get_auto_fix_queue(projectId: str) -> list[dict[str, Any]]:
    """Return the AutoFix queue items."""
    return auto_fix_service.get_queue(projectId)


@router.post("/{projectId}/auto-fix/check-new")
async def check_new_issues(projectId: str) -> dict[str, Any]:
    """Manually trigger a poll: find new issues + start each one.

    This is what the frontend's "Poll now" / refresh button calls,
    and also what the 5-min auto-poll loop in ``useAutoFix.ts`` calls
    when the toggle is enabled.

    The auto-poll in the frontend is per-project + only runs while the
    user has the GitHub Issues view open — there's intentionally no
    server-side cron.  This endpoint is the polling primitive both
    invocations share.
    """
    try:
        result = await auto_fix_service.check_new_and_start_all(projectId)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("[auto_fix] check_new_issues failed project=%s", projectId)
        raise HTTPException(status_code=500, detail=f"check failed: {e}")
    return result


@router.post("/{projectId}/auto-fix/{issueNumber}/start")
async def start_auto_fix_one(projectId: str, issueNumber: int) -> dict[str, Any]:
    """Import + start the agent on a single issue."""
    try:
        return await auto_fix_service.start_auto_fix(projectId, issueNumber)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(
            "[auto_fix] start_auto_fix failed project=%s issue=%d",
            projectId, issueNumber,
        )
        raise HTTPException(status_code=500, detail=f"start failed: {e}")
