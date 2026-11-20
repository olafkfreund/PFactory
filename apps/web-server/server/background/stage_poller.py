"""Background poller for draft-PR issue stage approvals (#1096).

Scans active issue-stage sessions whose draft PR is waiting on a bound
``/approve <stage>`` comment and runs one guarded ``poll_issue_stage`` tick
per matching session in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import cast

_BACKEND_PATH = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PATH))

from plan import service as plan_service  # noqa: E402
from plan.service import EmitInProgressError, PlanServiceError, StaleSessionError  # noqa: E402

logger = logging.getLogger(__name__)

_ACTIVE_STAGES = frozenset({"intent", "spec", "plan"})


def poll_active_issue_stages(
    service: plan_service.PlanService | None = None,
) -> list[str]:
    """Poll every active draft-PR issue stage session once. Sync, thread-safe."""
    svc = service or cast("plan_service.PlanService", plan_service.SERVICE)
    sessions, _ = svc._list_sessions()
    polled: list[str] = []
    for session in sessions:
        if session.status in {"discarded", "rejected", "failed"}:
            continue
        raw = session.stage_state
        if not isinstance(raw, dict):
            continue
        if (
            raw.get("pr_number")
            and raw.get("current_stage") in _ACTIVE_STAGES
            and raw.get("stage_status", "draft") == "draft"
        ):
            try:
                svc.poll_issue_stage(session.session_id)
                polled.append(session.session_id)
            except (EmitInProgressError, StaleSessionError):
                logger.debug("stage poll skipped busy session %s", session.session_id)
            except PlanServiceError as exc:
                logger.warning(
                    "stage poll failed for session %s (%s)",
                    session.session_id,
                    type(exc).__name__,
                )
            except Exception as exc:  # noqa: BLE001 - one bad session must not halt the sweep
                logger.warning(
                    "unexpected stage poll error for %s (%s)",
                    session.session_id,
                    type(exc).__name__,
                )
    return polled


async def stage_poller_loop(interval_seconds: float = 30.0) -> None:
    """Run :func:`poll_active_issue_stages` every ``interval_seconds`` until cancelled."""
    logger.info("stage poller loop started (every %ss)", interval_seconds)
    try:
        while True:
            try:
                await asyncio.to_thread(poll_active_issue_stages)
            except Exception:  # keep the loop alive across transient errors
                logger.exception("stage poller iteration failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("stage poller loop cancelled")
        raise
