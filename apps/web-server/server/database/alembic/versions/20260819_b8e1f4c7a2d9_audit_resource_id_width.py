"""audit_logs.resource_id holds any resource's id, not a UUID

Revision ID: b8e1f4c7a2d9
Revises: a7d2e4b8c1f3
Create Date: 2026-08-19

``resource_id`` was declared ``String(36)`` -- exactly a UUID's length, copied
from the ``id`` columns beside it. But it carries NO foreign key and sits next
to ``resource_type: String(255)``: it is a free-form pointer to a row in
whichever table ``resource_type`` names, and those tables do not all use UUID
keys.

The task pipeline builds a composite id, ``"{project_id}:pending-{hex8}"``,
which is 53 characters for a GitHub-backed project. Every audited task action
therefore failed with StringDataRightTruncationError -- AFTER the API had
already returned ``{"success": true}``, so the caller was told the task existed
when no row had been written.

``audit_logs`` was EMPTY at the time of this migration (0 rows), which is itself
the evidence: no audited action had ever been recorded successfully.

Widening to 255 matches ``resource_type`` and is a pure relaxation -- no
existing value can fail to fit, so the downgrade is only safe while every stored
value is short enough, which it asserts rather than assumes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e1f4c7a2d9"
down_revision: str | None = "a7d2e4b8c1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table for SQLite portability -- SQLite has no ALTER COLUMN, so
    # a bare op.alter_column is a syntax error there while being fine on
    # Postgres. The test suite migrates against SQLite. Same pattern as
    # c6e3b2d4a8f0.
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "resource_id",
            existing_type=sa.String(36),
            type_=sa.String(255),
            existing_nullable=True,
        )


def downgrade() -> None:
    # Refuse rather than truncate: narrowing silently destroys audit references.
    conn = op.get_bind()
    too_long = conn.execute(
        sa.text("SELECT count(*) FROM audit_logs WHERE char_length(resource_id) > 36")
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            f"{too_long} audit_logs row(s) have resource_id longer than 36 chars; "
            "narrowing would truncate audit references. Resolve those rows first."
        )
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "resource_id",
            existing_type=sa.String(255),
            type_=sa.String(36),
            existing_nullable=True,
        )
