"""add oidc_sub column to users

P3.3 of Epic #26 — adds the stable OIDC subject identifier column so
that successive logins for the same IdP user resolve to the same User
row. Nullable so existing locally-registered users (no SSO) are
unaffected; unique so an IdP user can't accidentally fork into two
local accounts.

Revision ID: d8f1a3c5e7b9
Revises: c6e3b2d4a8f0
Create Date: 2026-05-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8f1a3c5e7b9"
down_revision: Union[str, Sequence[str], None] = "c6e3b2d4a8f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table for SQLite compatibility — SQLite can't add a
    # UNIQUE constraint via plain ALTER TABLE, but batch mode re-creates
    # the table with the new column + constraint atomically. On
    # Postgres the batch reduces to a regular ALTER TABLE.
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("oidc_sub", sa.String(length=255), nullable=True)
        )
        batch.create_unique_constraint("uq_users_oidc_sub", ["oidc_sub"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_oidc_sub", type_="unique")
        batch.drop_column("oidc_sub")
