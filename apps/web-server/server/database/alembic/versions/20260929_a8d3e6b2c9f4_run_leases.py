"""run_leases: which replica owns a running task, reply, changelog or review (#805)

The four services kept "what is running" in per-process dicts, so with more
than one replica the status, the duplicate-run guard and stop were all wrong
on the pod that did not start the run. Additive only; older code ignores it.

Revision ID: a8d3e6b2c9f4
Revises: f2c7a9d4e1b3
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8d3e6b2c9f4"
down_revision: str | Sequence[str] | None = "f2c7a9d4e1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_leases",
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("run_leases")
