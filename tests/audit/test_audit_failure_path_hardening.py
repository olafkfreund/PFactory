"""``log_audit_event`` must keep both of its promises on the failure path.

Two defects, one function, both on the path that fires when the audit row
could NOT be written -- i.e. exactly when a reader is least able to spot a
problem from the log itself.

Finding A -- log injection. ``resource_id`` is deliberately free-form
``String(255)``: a pointer into whichever table ``resource_type`` names. A
caller-controlled id carrying a newline plus a plausible timestamp forges a
whole log line the SIEM reads as real. ``log_audit_event_bg``, a few lines
below in the same module, already routes all three through ``sanitize_log``;
the sibling did not.

Finding B -- a swallowed failure that is not actually swallowed. ``db.add`` +
``db.flush`` sat directly inside the try, so a violated FK left the CALLER's
``AsyncSession`` in a needs-rollback state and the route's next ``commit()``
raised ``PendingRollbackError``. The docstring promises audit failures never
propagate; before the savepoint they propagated as a 500 on the business
request.

Both tests assert on the OBSERVABLE consequence, not on a helper's return
value: Finding A reads the LOG FILE and counts lines (a record that still
writes two lines passes any assertion made against a sanitised string), and
Finding B commits the caller's own row and reads it back.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

FORGED = "2026-09-05 12:00:00 WARNING server.auth: token accepted for admin"
INJECTED_RESOURCE_ID = f"task-1\n{FORGED}"


class _ExplodingSession:
    """A session whose flush fails without echoing its input.

    The exception text is deliberately payload-free. ``logger.warning(...,
    exc_info=True)`` renders the traceback into the same record, so an
    exception carrying the raw ``resource_id`` (an ``IntegrityError`` quoting
    its bind parameters, say) would forge the line through the traceback no
    matter how well the message args are sanitised. That is a real and
    separate hole; this test is scoped to the message args so a failure here
    names one cause.
    """

    async def execute(self, *_a, **_k):
        class _R:
            @staticmethod
            def scalar_one_or_none():
                return None

        return _R()

    def begin_nested(self):
        session = self

        class _Savepoint:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        return _Savepoint()

    def add(self, _entry: object) -> None:
        return None

    async def flush(self) -> None:
        raise RuntimeError("audit insert rejected")


def _read_lines(log_path: Path) -> list[str]:
    return log_path.read_text(encoding="utf-8").splitlines()


@pytest.mark.audit
def test_failure_path_cannot_forge_a_log_line(tmp_path: Path) -> None:
    """A newline in ``resource_id`` must not become its own line on disk."""
    from server.services import audit_service

    log_path = tmp_path / "audit.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger = logging.getLogger(audit_service.__name__)
    logger.addHandler(handler)
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:

        async def _go() -> None:
            await audit_service.log_audit_event(
                db=_ExplodingSession(),  # type: ignore[arg-type]
                action="user.login",
                resource_type="user",
                resource_id=INJECTED_RESOURCE_ID,
            )

        asyncio.new_event_loop().run_until_complete(_go())
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate

    lines = _read_lines(log_path)
    assert any("Failed to write audit log entry" in line for line in lines), (
        "the failure was not logged at all -- this test proves nothing unless "
        "the record it inspects was actually written"
    )
    assert not any(line.startswith(FORGED) for line in lines), (
        "resource_id forged its own log line: a SIEM parsing this file reads "
        f"{FORGED!r} as a genuine auth event. Route resource_id through "
        "sanitize_log, as log_audit_event_bg already does.\n"
        + "\n".join(repr(line) for line in lines)
    )


@pytest.mark.audit
def test_audit_failure_leaves_the_caller_able_to_commit(fresh_db) -> None:
    """The swallowed audit failure must not poison the caller's session.

    Fails on the pre-savepoint code with ``PendingRollbackError`` raised by
    the CALLER's commit -- the business write, not the audit write.
    """
    from server.database.models import AuditLog, Organization
    from server.services.audit_service import log_audit_event
    from sqlalchemy import select

    _engine, SessionLocal = fresh_db
    org_name = "savepoint-probe"

    async def _go() -> None:
        async with SessionLocal() as session:
            session.add(
                Organization(name=org_name, slug=org_name, owner_id="owner-1")
            )

            # A NOT NULL violation on `action`: one of the concrete ways the
            # insert fails in production (an over-length resource_id and a
            # violated org_id FK are the others), and the only one SQLite
            # enforces -- it ignores VARCHAR widths and has foreign keys off.
            await log_audit_event(
                db=session,
                action=None,  # type: ignore[arg-type]
                resource_type="org",
                resource_id="savepoint-probe",
            )

            # The business commit. This is the assertion: it raised
            # PendingRollbackError before the audit insert got a savepoint.
            await session.commit()

        async with SessionLocal() as check:
            orgs = (
                (await check.execute(select(Organization).where(Organization.name == org_name)))
                .scalars()
                .all()
            )
            assert len(orgs) == 1, (
                "the caller's own row did not survive a failed audit write; "
                "audit logging is supposed to be failure-safe for the caller"
            )
            audits = (await check.execute(select(AuditLog))).scalars().all()
            assert audits == [], (
                "the failed audit row landed anyway -- the savepoint is "
                "supposed to roll the bad insert back, not hide it"
            )

    asyncio.new_event_loop().run_until_complete(_go())
