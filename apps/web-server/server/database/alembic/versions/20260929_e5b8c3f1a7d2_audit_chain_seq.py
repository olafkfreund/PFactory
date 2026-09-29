"""audit_logs.chain_seq: an unambiguous hash-chain order (#806)

The chain was ordered by `created_at`, which is the transaction start time in
Postgres, so concurrent writers could verify out of order (and ties had no
tie-break). `chain_seq` is set by the app under the audit-chain lock.

Existing rows are numbered in `created_at, id` order, the order their chain was
built and verified in, so it still verifies.

Revision ID: e5b8c3f1a7d2
Revises: d4a7e2b9f1c6
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b8c3f1a7d2"
down_revision: str | Sequence[str] | None = "d4a7e2b9f1c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("chain_seq", sa.BigInteger(), nullable=True))
    # Correlated subquery rather than UPDATE ... FROM: portable to SQLite.
    op.execute(
        "UPDATE audit_logs SET chain_seq = ("
        " SELECT o.rn FROM ("
        "  SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS rn FROM audit_logs"
        " ) AS o WHERE o.id = audit_logs.id)"
    )
    # batch_alter_table: SQLite has no ALTER COLUMN (same pattern as b8e1f4c7a2d9).
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column("chain_seq", existing_type=sa.BigInteger(), nullable=False)
    op.create_index("ux_audit_logs_chain_seq", "audit_logs", ["chain_seq"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_audit_logs_chain_seq", table_name="audit_logs")
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_column("chain_seq")
