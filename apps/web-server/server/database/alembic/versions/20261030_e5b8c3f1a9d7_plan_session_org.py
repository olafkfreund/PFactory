"""plan_sessions lazy org_id column (#968)

Sessions created before #968 have no `org_id` in their JSON payload and derive
it on first read from the project registered for `session.repo`. Persisting that
resolution inside the JSON payload would bump `version`, causing an in-flight
`process()` holding an earlier copy of the session to fail its compare-and-set
with `StaleSessionError`.

- `org_id`: nullable write-once column outside `payload` and `version`, so a
  read-time stamp never invalidates an in-flight pipeline write.

Additive only; older code ignores the column.

Revision ID: e5b8c3f1a9d7
Revises: c7e4a2d9b1f3
Create Date: 2026-10-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b8c3f1a9d7"
down_revision: str | Sequence[str] | None = "c7e4a2d9b1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plan_sessions", sa.Column("org_id", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("plan_sessions", "org_id")
