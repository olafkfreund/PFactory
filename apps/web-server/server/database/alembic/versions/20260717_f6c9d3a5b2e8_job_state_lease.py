"""job_states.lease_expires_at — reclaim slots stranded by a killed pod (#300)

A ``running`` row only frees its admission slot when the owning process lives
long enough to stamp a terminal status. A SIGKILL (OOM, eviction, node loss,
liveness restart) runs no cleanup code, so the row stayed ``running`` forever
and permanently burned one of ``PFACTORY_MAX_CONCURRENT_PLANS`` slots for the
whole fleet. The owner now holds a renewable lease; an expired lease is the
(only possible) signal that it died, and the row is reclaimed.

Live-DB safety: adding a NULLable column with no default and no index is a
metadata-only change on Postgres — no table rewrite, no lock held for a scan.

Backfill: rows already ``running`` when this lands predate any lease. NULL is
never reclaimed (see ``store._reclaim_on``), so leaving them NULL would strand
the very rows this fixes. Instead they get a one-off grace lease: a genuinely
live plan carried across the rolling deploy keeps renewing it (new code) and
survives, while a stranded row has nobody to renew it and is reclaimed once
the grace lapses. The grace is deliberately wider than the default 600s TTL so
this migration can never reclaim a healthy in-flight plan.

Revision ID: f6c9d3a5b2e8
Revises: e5b8c2f1a9d4
Create Date: 2026-07-17
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6c9d3a5b2e8"
down_revision: str | Sequence[str] | None = "e5b8c2f1a9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Grace for rows that were already `running` at migration time.
_BACKFILL_GRACE = timedelta(minutes=15)


def upgrade() -> None:
    op.add_column(
        "job_states",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Bind the deadline as a Python value rather than a dialect-specific SQL
    # interval expression (`now() + interval '15 minutes'` vs sqlite's
    # `datetime('now', ...)`), so this runs identically on Postgres and the
    # SQLite lane.
    op.execute(
        sa.text(
            "UPDATE job_states SET lease_expires_at = :deadline WHERE lifecycle_state = 'running'"
        ).bindparams(deadline=datetime.now(UTC) + _BACKFILL_GRACE)
    )


def downgrade() -> None:
    op.drop_column("job_states", "lease_expires_at")
