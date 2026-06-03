"""Periodic liveness sweep — the web-server driver for the #95 watchdog.

The backend ships the watchdog (``agents.liveness``) and a one-shot sweep
(``agents.liveness_sweep.sweep``); this is the long-running driver that calls
the sweep on a timer while the portal is up, so a stage that hangs (the
"Stream closed" failure) gets flagged ``stalled`` automatically instead of
sitting "stuck" forever.

Started from the app lifespan and gated by ``APP_LIVENESS_SWEEP_ENABLED``
(default OFF). The periodic body is factored into ``run_one_sweep`` so it can
be unit-tested without spawning the loop; the sync sweep runs in a thread so
it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# The web-server's PYTHONPATH doesn't reliably include apps/backend, so add it
# explicitly at import time — the canonical pattern across the server (see
# routes/mcp.py, services/auto_fix_service.py).
_BACKEND_PATH = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PATH))

from agents.liveness_sweep import sweep  # noqa: E402  (after sys.path insert)

logger = logging.getLogger(__name__)


def run_one_sweep(deadline_seconds: float | None = None) -> list:
    """Run a single liveness sweep and log anything flagged. Testable, sync.

    Returns the ``(spec_dir, verdict)`` list from the backend sweep.
    """
    results = sweep(deadline_seconds=deadline_seconds)
    stalled = [(d, v) for d, v in results if v.stalled]
    for spec_dir, verdict in stalled:
        logger.warning("liveness: flagged %s as stalled (%s)", spec_dir, verdict.reason)
    if results:
        logger.info(
            "liveness sweep: %d task(s) checked, %d stalled", len(results), len(stalled)
        )
    return results


async def liveness_sweep_loop(
    interval_seconds: float, deadline_seconds: float | None = None
) -> None:
    """Run :func:`run_one_sweep` every ``interval_seconds`` until cancelled.

    The sync sweep runs in a worker thread so filesystem I/O never blocks the
    event loop. Per-iteration errors are logged and swallowed so the loop
    survives a transient failure; cancellation propagates cleanly on shutdown.
    """
    logger.info("liveness sweep loop started (every %ss)", interval_seconds)
    try:
        while True:
            try:
                await asyncio.to_thread(run_one_sweep, deadline_seconds)
            except Exception:  # noqa: BLE001 - a bad iteration must not kill the loop
                logger.exception("liveness sweep iteration failed")
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("liveness sweep loop cancelled")
        raise
