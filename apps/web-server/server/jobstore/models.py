"""ORM model for the shared job-state record (RFC-0016 #217).

One row per PFactory plan job, conforming to the Factory hub schema
``apis/job-state.schema.json`` (``service="pfactory"``, ``kind="plan"``).
Reuses the web-server's declarative :class:`Base` so the table is part of
``Base.metadata`` (Alembic + ``create_all`` in tests both see it).

Design notes:
  - ``job_id`` is the primary key (PFactory assigns it: the plan_id).
  - Structured sub-objects from the schema (``admission``, ``result``,
    ``usage``, ``worker_ref``, ``artifacts``) are stored as JSON columns —
    portable across SQLite (tests) and Postgres (prod) via SQLAlchemy's
    generic ``JSON`` type. Per the conventions, artifacts carry URIs only,
    never inline blobs.
  - ``lifecycle_state`` is the canonical taxonomy value; ``service_status``
    keeps PFactory's raw status alongside it.
  - Timestamps are timezone-aware UTC. ``ended_at`` is set on terminal
    transitions; ``error`` is required when failed/stuck (enforced by the
    store, not the column, so a partial write can't violate the DB).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..database.models import Base

# Schema version from apis/job-state.schema.json (`schema_version` const "1").
JOB_STATE_SCHEMA_VERSION = "1"


class JobState(Base):
    """A durable RFC-0016 job-state row for one PFactory plan job."""

    __tablename__ = "job_states"

    # schema_version (const "1" today; bumped only on a breaking change).
    schema_version: Mapped[str] = mapped_column(
        String(8), nullable=False, default=JOB_STATE_SCHEMA_VERSION
    )
    # job_id — PFactory's session_id / plan_id. Primary key.
    job_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    # correlation_key — RFC-0001 thread key (the emitted GitHub issue#, or a
    # synthetic pf-<id>). Stored as text so an integer issue# or a synthetic
    # string both fit. Indexed for cross-service joins.
    correlation_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # tenant_id — multi-tenancy (#308). Every pre-existing row (and every
    # single-tenant write) is "default"; indexed so tenant-scoped reads are
    # cheap. server_default doubles as the migration backfill.
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="default",
        server_default="default",
        index=True,
    )
    # service / kind are constant for this table but stored so the row is a
    # faithful, self-describing job-state record (the schema requires them).
    service: Mapped[str] = mapped_column(String(32), nullable=False, default="pfactory")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="plan")
    # Canonical lifecycle_state (queued/running/review/done/failed/stuck).
    # Indexed because the admission cap + replica-recovery query filter on it.
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # Raw PFactory-native status (e.g. 'processing', 'emitted').
    service_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Optional finer-grained phase.
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Execution attempt counter (RFC-0008 failover / restart increments).
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # admission{enqueued_at, queue_position, started_at} as JSON.
    admission: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # worker_ref (null in Phase 1 in-pod subprocess).
    worker_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # artifacts[] — object-store URI references (never blobs).
    artifacts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Terminal result payload (service-specific). Set on done/failed.
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # usage accounting (RFC-0014 billing modes).
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Human-readable failure reason; required when failed/stuck.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lease expiry (#300). A `running` row is only trusted until this instant;
    # its owner renews it while process() runs. A SIGKILL/OOM/eviction runs NO
    # cleanup code, so the lease lapsing is the ONLY signal that the owner died
    # — an expired `running` row is reclaimed (-> failed) so its admission slot
    # is not leaked forever. NULL means "no lease" and is never reclaimed.
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Timestamps (UTC). created/updated default server-side; ended on terminal.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<JobState job_id={self.job_id!r} "
            f"lifecycle_state={self.lifecycle_state!r} "
            f"service_status={self.service_status!r}>"
        )
