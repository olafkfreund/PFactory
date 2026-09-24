"""plan_sessions emit lease and row version (#758)

Two replicas could both emit the same session to GitHub (the emit lock was a
per-process `threading.Lock`), and a replica holding an older copy of a session
could overwrite a newer write from another pod.

- `emit_lease_owner` / `emit_lease_until`: a cross-replica lease taken with one
  atomic `UPDATE ... WHERE lease is free or expired RETURNING`. It expires, so
  a pod that dies mid-emit does not wedge the session.
- `version`: bumped on every write; `upsert` is a compare-and-set on it.

Additive only; older code ignores the columns.

Revision ID: d4a7e2b9f1c6
Revises: c9f2a6d1e483
Create Date: 2026-09-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7e2b9f1c6"
down_revision: str | Sequence[str] | None = "c9f2a6d1e483"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plan_sessions", sa.Column("emit_lease_owner", sa.String(128), nullable=True))
    op.add_column(
        "plan_sessions", sa.Column("emit_lease_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "plan_sessions",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("plan_sessions", "version")
    op.drop_column("plan_sessions", "emit_lease_until")
    op.drop_column("plan_sessions", "emit_lease_owner")
