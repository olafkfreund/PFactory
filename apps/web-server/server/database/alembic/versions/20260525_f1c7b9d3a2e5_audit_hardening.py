"""audit hardening: retention_until + prev_hash + gdpr_erased_at + nullable PII

P5.1 of Epic #26 — extends the audit_logs table with retention and
hash-chain columns, adds users.gdpr_erased_at, and relaxes
users.email + users.name to nullable to support GDPR erasure.

Revision ID: f1c7b9d3a2e5
Revises: e9c4b6a2f8d1
Create Date: 2026-05-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1c7b9d3a2e5"
down_revision: Union[str, Sequence[str], None] = "e9c4b6a2f8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # audit_logs: retention_until + prev_hash + index on retention_until.
    with op.batch_alter_table("audit_logs") as batch:
        batch.add_column(
            sa.Column("retention_until", sa.DateTime(), nullable=True)
        )
        batch.add_column(
            sa.Column("prev_hash", sa.String(length=64), nullable=True)
        )
        batch.create_index(
            "ix_audit_logs_retention_until",
            ["retention_until"],
        )

    # users: gdpr_erased_at + relax PII columns to nullable for erasure.
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("gdpr_erased_at", sa.DateTime(), nullable=True)
        )
        batch.alter_column(
            "email",
            existing_type=sa.String(length=255),
            nullable=True,
        )
        batch.alter_column(
            "name",
            existing_type=sa.String(length=255),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "name",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch.alter_column(
            "email",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch.drop_column("gdpr_erased_at")

    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_index("ix_audit_logs_retention_until")
        batch.drop_column("prev_hash")
        batch.drop_column("retention_until")
