"""oauth_connect_states: the email OAuth connect state, shared by replicas (#807)

The state lived in a per-process dict, so a provider callback that reached
another pod was rejected. Additive only; older code ignores the table.

Revision ID: f2c7a9d4e1b3
Revises: e5b8c3f1a7d2
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c7a9d4e1b3"
down_revision: str | Sequence[str] | None = "e5b8c3f1a7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_connect_states",
        sa.Column("state", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_connect_states_expires_at", "oauth_connect_states", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_connect_states_expires_at", table_name="oauth_connect_states")
    op.drop_table("oauth_connect_states")
