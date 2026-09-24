"""plan_sessions — the authoritative plan-session store (#755)

`PlanService` loaded every session into a per-process dict at startup and read
only from it, so under >1 replica a write on one pod was invisible to the
others (measured: four pods, one reporting `discarded`, three `ingested`) and
`self._seq = len(self._sessions)` could mint the same id twice. The JSON files
on the PVC cannot fix that: the volume is ReadWriteOnce/local-path, so replicas
share them only while co-scheduled.

This table is the shared copy every replica reads through. `payload` carries
`PlanSession.model_dump_json()` verbatim, so the model stays the single
definition of a session's shape. `seq` exists so ids are allocated in one
transaction instead of from a per-process counter.

Deliberately its own table rather than a column on `job_states`: that table is
fleet-shared (aifactory/tfactory/pfactory rows), and a PFactory plan payload
does not belong in every service's row.

`tenant_id` follows the `job_states` convention (#308): String(64), NOT NULL,
server_default 'default', indexed — so pre-existing rows need no backfill pass
and tenant-scoped list reads have an index.

Revision ID: c9f2a6d1e483
Revises: b8e1f4c7a2d9
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9f2a6d1e483"
down_revision: str | Sequence[str] | None = "b8e1f4c7a2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_sessions",
        sa.Column("session_id", sa.String(length=255), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        # The numeric prefix of `NNN-slug`, so next_seq() can allocate the next
        # id in one transaction (max(seq)+1 FOR UPDATE) instead of per process.
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_version", sa.String(length=8), nullable=False, server_default="1"),
        # PlanSession.model_dump_json() verbatim — the pydantic model remains
        # the one definition of a session's shape. Text, not JSON: a JSON column
        # would re-encode the string.
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_plan_sessions_tenant_id", "plan_sessions", ["tenant_id"])
    # list_sessions() orders by the numeric prefix; the cockpit polls it.
    op.create_index("ix_plan_sessions_seq", "plan_sessions", ["seq"])

    # Session ids come from an atomic counter, not max(seq)+1: allocation and
    # the session insert are separate transactions, so two replicas could read
    # the same maximum before either inserted (and Postgres rejects FOR UPDATE
    # with an aggregate outright). One row, UPDATE ... RETURNING.
    op.create_table(
        "plan_session_seq",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("INSERT INTO plan_session_seq (id, value) VALUES (1, 0)")


def downgrade() -> None:
    op.drop_table("plan_session_seq")
    op.drop_index("ix_plan_sessions_seq", table_name="plan_sessions")
    op.drop_index("ix_plan_sessions_tenant_id", table_name="plan_sessions")
    op.drop_table("plan_sessions")
