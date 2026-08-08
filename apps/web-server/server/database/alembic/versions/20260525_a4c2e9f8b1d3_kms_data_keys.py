"""kms_data_keys

P2.2 of Epic #26 — per-organization wrapped data keys backing the
EncryptedString TypeDecorator. See guides/plans/2026-05-24-pfactory-
enterprise-v1-design.md §3.1.3 for the design rationale.

Revision ID: a4c2e9f8b1d3
Revises: 1b386c99e615
Create Date: 2026-05-25

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c2e9f8b1d3"
down_revision: Union[str, Sequence[str], None] = "1b386c99e615"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kms_data_keys",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column(
            "kms_key_id",
            sa.String(length=255),
            nullable=False,
            comment=(
                "Identifier of the KMS root key that wrapped this data key. "
                "Lets rotation runbooks know which backend wrapped each row."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "rotated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment=(
                "Updated on every re-wrap (root key rotation). "
                "DataKeyManager polls this to invalidate its LRU cache."
            ),
        ),
    )
    op.create_index("ix_kms_data_keys_org_id", "kms_data_keys", ["org_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_kms_data_keys_org_id", table_name="kms_data_keys")
    op.drop_table("kms_data_keys")
