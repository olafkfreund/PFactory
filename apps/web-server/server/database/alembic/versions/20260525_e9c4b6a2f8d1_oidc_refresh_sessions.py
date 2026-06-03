"""add oidc_refresh_sessions table

P3.4 of Epic #26 — tracks OIDC-issued refresh tokens so the refresh
path can re-validate against the IdP's userinfo endpoint and propagate
revocation within the access-token TTL window (15 min).

Revision ID: e9c4b6a2f8d1
Revises: d8f1a3c5e7b9
Create Date: 2026-05-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9c4b6a2f8d1"
down_revision: Union[str, Sequence[str], None] = "d8f1a3c5e7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oidc_refresh_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("oidc_sub", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "last_validated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti"),
    )
    op.create_index(
        "ix_oidc_refresh_sessions_user_id",
        "oidc_refresh_sessions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_oidc_refresh_sessions_user_id", table_name="oidc_refresh_sessions"
    )
    op.drop_table("oidc_refresh_sessions")
