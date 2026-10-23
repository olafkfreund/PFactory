"""Store board-facing task snapshots in the shared database (#905).

Revision ID: c7e4a2d9b1f3
Revises: b3d9e1f7a5c2
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e4a2d9b1f3"
down_revision: str | Sequence[str] | None = "b3d9e1f7a5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_board_states",
        sa.Column("task_id", sa.String(255), primary_key=True),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_task_board_states_project_id", "task_board_states", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_task_board_states_project_id", table_name="task_board_states")
    op.drop_table("task_board_states")
