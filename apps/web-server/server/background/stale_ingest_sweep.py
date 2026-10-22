"""Periodically discard ingested plan sessions older than the TTL (#808)."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import cast

_BACKEND_PATH = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PATH))

from plan import service as plan_service  # noqa: E402  (after sys.path insert)

logger = logging.getLogger(__name__)


async def stale_ingest_sweep_loop(ttl_seconds: float, interval_seconds: float = 3600) -> None:
    """Discard stale ingested sessions every interval until cancelled."""
    # SERVICE is a lazy module attribute (PEP 562), typed `object` by its __getattr__.
    service = cast("plan_service.PlanService", plan_service.SERVICE)
    while True:
        try:
            discarded = await asyncio.to_thread(service.discard_stale_ingested, ttl_seconds)
            if discarded:
                logger.info("stale ingest sweep discarded %d session(s)", len(discarded))
        except Exception:  # a bad iteration must not kill the loop
            logger.exception("stale ingest sweep iteration failed")
        await asyncio.sleep(interval_seconds)
