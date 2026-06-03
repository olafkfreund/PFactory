"""Daily audit-log retention job (Epic #26 P5.6).

Deletes audit_logs rows where ``retention_until <= now()``. The
default retention policy is 13 months (SOC2 12mo + buffer), set
per-row at write time so future per-action policies remain possible.

Invocation:
  - As a CLI: ``python -m server.jobs.audit_retention``
  - As an async function from an in-process scheduler.

For Helm-based deployments the operator runs this as a Kubernetes
CronJob; the templated CronJob lands in v1.0.1. For now it's a
documented manual procedure (see guides/operations/audit-trail.md).

Re-chaining after deletion: the retention job does NOT re-chain the
audit log. Older rows are removed from the chain head, which is the
expected pruning behavior — verify_chain run against the surviving
window still succeeds (the first surviving row's prev_hash refers
to a deleted row, and the verifier accepts this as a valid genesis
of the pruned window).

The verify_chain function is strict by default — to support pruned
windows in v1.1, we'll add a ``genesis_override`` parameter so
operators can declare the chain head explicitly. For v1.0, prune
+ verify is a documented two-step operator workflow.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.engine import async_session_factory
from ..database.models import AuditLog

logger = logging.getLogger(__name__)


async def run_retention(
    db: AsyncSession, *, as_of: datetime | None = None
) -> dict:
    """Delete rows where retention_until <= ``as_of``. Returns a summary."""
    now = as_of or datetime.utcnow()
    # Count first so the summary can report a clean before/after.
    count_q = select(func.count(AuditLog.id)).where(
        AuditLog.retention_until.is_not(None),
        AuditLog.retention_until <= now,
    )
    expired_count = (await db.execute(count_q)).scalar() or 0

    if expired_count:
        await db.execute(
            delete(AuditLog).where(
                AuditLog.retention_until.is_not(None),
                AuditLog.retention_until <= now,
            )
        )
        await db.commit()

    total_q = select(func.count(AuditLog.id))
    remaining = (await db.execute(total_q)).scalar() or 0

    logger.info(
        "audit retention: as_of=%s deleted=%d remaining=%d",
        now.isoformat(), expired_count, remaining,
    )
    return {
        "as_of": now.isoformat(),
        "deleted": expired_count,
        "remaining": remaining,
    }


async def _main() -> int:
    if async_session_factory is None:
        print("DATABASE_URL not configured", flush=True)
        return 2
    async with async_session_factory() as session:
        summary = await run_retention(session)
    print(summary, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
