"""job_states table — durable RFC-0016 job-state store (PFactory #217)

One row per PFactory plan job, conforming to the Factory hub schema
``apis/job-state.schema.json`` (service="pfactory", kind="plan"). Replaces
the per-pod in-memory ``_sessions`` dict + optional JSON mirror so plan
lifecycle + the admission cap/queue are durable and multi-replica-safe.

Structured sub-objects (admission, worker_ref, artifacts, result, usage)
are JSON columns — portable across SQLite (tests) and Postgres (prod) via
SQLAlchemy's generic JSON type. ``lifecycle_state`` + ``correlation_key``
are indexed: the admission cap and replica-recovery query filter on
lifecycle_state, and correlation_key threads the job across services.

Revision ID: e5b8c2f1a9d4
Revises: d4a7c1e6b9f2
Create Date: 2026-06-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b8c2f1a9d4"
down_revision: str | Sequence[str] | None = "d4a7c1e6b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_states",
        sa.Column("schema_version", sa.String(length=8), nullable=False),
        sa.Column("job_id", sa.String(length=255), primary_key=True, nullable=False),
        sa.Column("correlation_key", sa.String(length=255), nullable=True),
        sa.Column("service", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=16), nullable=False),
        sa.Column("service_status", sa.String(length=64), nullable=True),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("admission", sa.JSON(), nullable=True),
        sa.Column("worker_ref", sa.JSON(), nullable=True),
        sa.Column("artifacts", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_job_states_lifecycle_state", "job_states", ["lifecycle_state"])
    op.create_index("ix_job_states_correlation_key", "job_states", ["correlation_key"])


def downgrade() -> None:
    op.drop_index("ix_job_states_correlation_key", table_name="job_states")
    op.drop_index("ix_job_states_lifecycle_state", table_name="job_states")
    op.drop_table("job_states")
