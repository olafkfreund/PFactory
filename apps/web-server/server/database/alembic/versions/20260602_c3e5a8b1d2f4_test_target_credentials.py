"""test_target_credentials table for test-target auth (#107)

Adds the ``test_target_credentials`` table that stores encrypted credentials
used to authenticate to a system-under-test. Mirrors ``git_credentials``:
org-scoped, secret columns are LargeBinary (EncryptedString ciphertext at
rest), unique ``(org_id, name)`` so ``.pfactory.yml`` refs are unambiguous.

Revision ID: c3e5a8b1d2f4
Revises: b2d4f7e9c3a1
Create Date: 2026-06-02
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a8b1d2f4"
down_revision: str | Sequence[str] | None = "b2d4f7e9c3a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "test_target_credentials",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "kind", sa.String(length=50), nullable=False, server_default="form"
        ),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("secret", sa.LargeBinary(), nullable=False),
        sa.Column("extra", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_by",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("org_id", "name", name="uq_test_cred_org_name"),
    )
    op.create_index(
        "ix_test_target_credentials_org_id",
        "test_target_credentials",
        ["org_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_test_target_credentials_org_id",
        table_name="test_target_credentials",
    )
    op.drop_table("test_target_credentials")
