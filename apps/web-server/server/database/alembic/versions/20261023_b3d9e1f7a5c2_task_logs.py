"""Store task phase statuses and entries in the shared database (#829).

Revision ID: b3d9e1f7a5c2
Revises: a8d3e6b2c9f4
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d9e1f7a5c2"
down_revision: str | Sequence[str] | None = "a8d3e6b2c9f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_log_phases",
        sa.Column("task_id", sa.String(255), primary_key=True),
        sa.Column("phase", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.String(64)),
        sa.Column("completed_at", sa.String(64)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "task_log_entries",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True),
        sa.Column("task_id", sa.String(255), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("entry", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_task_log_entries_task_id_id", "task_log_entries", ["task_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_task_log_entries_task_id_id", table_name="task_log_entries")
    op.drop_table("task_log_entries")
    op.drop_table("task_log_phases")
