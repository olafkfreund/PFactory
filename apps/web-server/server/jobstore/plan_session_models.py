"""ORM model for the shared plan-session record (#755).

One row per plan session, holding the session payload every replica reads
through. `job_states` (beside this) carries the RFC-0016 *lifecycle* of a plan
job and is fleet-shared across aifactory/tfactory/pfactory; the plan payload
itself is PFactory's and lives here, so a fleet table does not grow a
service-specific blob.

Design notes:
  - ``payload`` is ``PlanSession.model_dump_json()`` verbatim, so the pydantic
    model stays the single definition of a session's shape; the store never
    reaches inside it.
  - ``seq`` is the numeric prefix of ``NNN-slug``. It exists so ids are
    allocated in one transaction (``max(seq)+1`` under a row lock) instead of
    from ``len(self._sessions)`` per process, which minted duplicates across
    replicas.
  - ``tenant_id`` follows the ``job_states`` convention (#308): NOT NULL,
    default "default", indexed for tenant-scoped list reads.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from server.database.models import Base

# Bumped only on a breaking change to the payload contract.
PLAN_SESSION_SCHEMA_VERSION = "1"


class PlanSessionCounter(Base):
    """A single row whose value is the last session number handed out (#755).

    Not ``max(seq)+1`` over :class:`PlanSessionRow`: allocation and the row
    insert happen in separate transactions, so two replicas can read the same
    maximum before either has inserted. (Postgres also rejects ``FOR UPDATE``
    with an aggregate, so the obvious lock does not even run.) An atomic
    ``UPDATE ... RETURNING`` on one row hands out a value that is never reused,
    whatever else is in flight.
    """

    __tablename__ = "plan_session_seq"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class PlanSessionRow(Base):
    """A durable plan session: the copy every replica reads through."""

    __tablename__ = "plan_sessions"

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="default",
        server_default="default",
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    schema_version: Mapped[str] = mapped_column(
        String(8), nullable=False, default=PLAN_SESSION_SCHEMA_VERSION
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), server_default=func.now()
    )
