"""docs_target_connections table for the docs-emit Settings (plan→docs P4b)

Stores a user's Backstage / Confluence connection used by the plan→docs emit.
Mirrors ``llm_endpoints``: user-scoped, the API token encrypted at rest
(EncryptedString ciphertext), unique ``(user_id, label)``.

Revision ID: d4a7c1e6b9f2
Revises: c3e5a8b1d2f4
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7c1e6b9f2"
down_revision: str | Sequence[str] | None = "c3e5a8b1d2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "docs_target_connections",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=50), nullable=False),  # backstage | confluence
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("api_token", sa.LargeBinary(), nullable=True),  # EncryptedString
        sa.Column("space", sa.String(length=255), nullable=True),  # Confluence
        sa.Column(
            "enabled_by_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "label", name="uq_docs_target_user_label"),
    )
    op.create_index(
        "ix_docs_target_connections_user_id",
        "docs_target_connections",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_docs_target_connections_user_id",
        table_name="docs_target_connections",
    )
    op.drop_table("docs_target_connections")
