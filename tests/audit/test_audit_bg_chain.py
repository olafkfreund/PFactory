"""#806: the background audit writer joins the hash chain.

``log_audit_event_bg`` (the MCP write routes' path) used to build its own row
with no ``prev_hash``, so every background row broke the chain.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.audit
def test_background_rows_join_the_chain(fresh_db, monkeypatch) -> None:
    from server.database.models import AuditLog
    from server.services import audit_service
    from server.services.audit_chain import row_as_mapping, verify_chain
    from sqlalchemy import select

    _engine, session_local = fresh_db
    monkeypatch.setattr(audit_service, "async_session_factory", session_local)

    async def _go() -> list[AuditLog]:
        async with session_local() as session:
            await audit_service.log_audit_event(db=session, action="test.fg", resource_type="t")
            await session.commit()
        await audit_service.log_audit_event_bg(action="test.bg", resource_type="t")
        async with session_local() as session:
            result = await session.execute(select(AuditLog).order_by(AuditLog.chain_seq))
            return list(result.scalars())

    rows = asyncio.new_event_loop().run_until_complete(_go())
    assert [r.action for r in rows] == ["test.fg", "test.bg"], "the background row was not written"
    assert [r.chain_seq for r in rows] == [1, 2]
    assert rows[1].prev_hash, "the background row has no prev_hash"
    assert rows[1].retention_until is not None
    ok, bad, reason = verify_chain([row_as_mapping(r) for r in rows])
    assert ok, f"chain broken at row {bad}: {reason}"
