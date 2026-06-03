"""git_credentials table for portal-managed clone auth (epic #82 PR-C)

Adds the ``git_credentials`` table that stores encrypted Personal
Access Tokens (and, in future iterations, Deploy Keys + GitHub App
install IDs) used by the portal's clone service when fetching private
repos. Tokens are encrypted at rest via the existing
``EncryptedString`` TypeDecorator (Epic #26 P2.3).

Revision ID: b2d4f7e9c3a1
Revises: f1c7b9d3a2e5
Create Date: 2026-05-27
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2d4f7e9c3a1"
down_revision: str | Sequence[str] | None = "f1c7b9d3a2e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "git_credentials",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "org_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        # Credential kind: ``pat`` (V1), forward-compatible with
        # ``deploy_key`` / ``github_app`` (later follow-ups).
        sa.Column(
            "kind",
            sa.String(length=50),
            nullable=False,
            server_default="pat",
        ),
        sa.Column("host", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        # Encrypted-at-rest via EncryptedString — stored as LargeBinary
        # in the DB schema (matches the pattern used by
        # email_accounts.access_token and llm_providers.api_key).
        sa.Column("token", sa.LargeBinary(), nullable=False),
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
    )
    op.create_index(
        "ix_git_credentials_org_id",
        "git_credentials",
        ["org_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_git_credentials_org_id", table_name="git_credentials")
    op.drop_table("git_credentials")
