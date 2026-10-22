"""Periodically discard ingested plan sessions older than the TTL (#808)."""

import asyncio
import logging
import sys
from pathlib import Path

_BACKEND_PATH = Path(__file__).resolve().parents[3] / "backend"
if str(_BACKEND_PATH) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PATH))

from plan.service import SERVICE  # noqa: E402  (after sys.path insert)

logger = logging.getLogger(__name__)


async def stale_ingest_sweep_loop(ttl_seconds: float, interval_seconds: float = 3600) -> None:
    """Discard stale ingested sessions every interval until cancelled."""
    while True:
        try:
            discarded = await asyncio.to_thread(SERVICE.discard_stale_ingested, ttl_seconds)
            if discarded:
                logger.info("stale ingest sweep discarded %d session(s)", len(discarded))
        except Exception:  # a bad iteration must not kill the loop
            logger.exception("stale ingest sweep iteration failed")
        await asyncio.sleep(interval_seconds)
