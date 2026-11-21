"""Permit org-scoped API keys without a user owner.

Revision ID: a1b2c3d4e5f6
Revises: e5b8c3f1a9d7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "e5b8c3f1a9d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=True)


def downgrade() -> None:
    if op.get_bind().execute(
        sa.text("SELECT count(*) FROM api_keys WHERE user_id IS NULL")
    ).scalar_one():
        raise RuntimeError("Ownerless API keys must be removed before downgrading")
    with op.batch_alter_table("api_keys") as batch:
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=False)
