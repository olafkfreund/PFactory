"""job_states.tenant_id — tenant-scope plan sessions (#308, factory-gitops#13)

Adds the multi-tenancy column: NOT NULL with server_default 'default', so
every pre-existing row is backfilled to the single "default" tenant in the
same statement (no separate UPDATE, no window where a row has no tenant).
Indexed because tenant-scoped list/detail reads filter on it.

Live-DB safety: on Postgres 11+ adding a NOT NULL column with a constant
default is a metadata-only change — no table rewrite.

Revision ID: a7d2e4b8c1f3
Revises: f6c9d3a5b2e8
Create Date: 2026-07-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d2e4b8c1f3"
down_revision: str | Sequence[str] | None = "f6c9d3a5b2e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_states",
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            nullable=False,
            server_default="default",
        ),
    )
    op.create_index("ix_job_states_tenant_id", "job_states", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_job_states_tenant_id", table_name="job_states")
    op.drop_column("job_states", "tenant_id")
