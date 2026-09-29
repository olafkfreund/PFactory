"""Erasing a terminal plan session, and hiding discarded ones by default (#798).

A nightly regression probe (``parr-regression-probe``, #360) leaves one
``discarded``/``rejected`` session behind per run. ``/discard`` gave those
sessions an honest exit off the board, but the rows themselves still
accumulate in the store forever — nothing removes them. This pins the delete
that does, and the trap a careless implementation falls into: reusing
``TERMINAL_STATUSES`` (which includes ``emitted``) would let a caller destroy
the audit trail for real GitHub epics.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))
_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from fastapi import HTTPException  # noqa: E402

from plan.service import PlanInputError, PlanService  # noqa: E402
from server.routes import plan_pipeline as pp  # noqa: E402

_PLAN = """# Refund flow
Add a refund flow to the orders web app.
## Acceptance Criteria
- A finance user can issue a refund
- The order status becomes refunded
"""


class _Request:
    """Header-carrying Request stand-in for direct route calls."""

    def __init__(self, headers: dict | None = None) -> None:
        self.headers = headers or {}


def _ingested(svc: PlanService | None = None, **kwargs) -> tuple[PlanService, str]:
    svc = svc or PlanService()
    return svc, svc.ingest_text(_PLAN, title="Refund flow", **kwargs).session_id


def _discarded(svc: PlanService | None = None, **kwargs) -> tuple[PlanService, str]:
    svc, sid = _ingested(svc, **kwargs)
    svc.discard(sid, actor="olafkfreund", reason="mis-ingested probe")
    return svc, sid


def _rejected(svc: PlanService | None = None, **kwargs) -> tuple[PlanService, str]:
    svc, sid = _ingested(svc, **kwargs)
    svc.process(sid)
    svc.reject(sid, approver="olafkfreund", feedback="no")
    return svc, sid


def _emitted(svc: PlanService | None = None, **kwargs) -> tuple[PlanService, str]:
    svc, sid = _ingested(svc, **kwargs)
    svc.process(sid)
    # Directly set the status rather than driving a real emit (#758's lease/
    # dry-run machinery is orthogonal here) — the established pattern, see
    # tests/test_hard_routing.py:119.
    svc.get(sid).status = "emitted"
    return svc, sid


# ── service: happy path ──────────────────────────────────────────────────────


def test_deleting_a_discarded_session_returns_the_summary_and_erases_it():
    svc, sid = _discarded()

    out = svc.delete_session(sid, actor="olafkfreund")

    assert out == {"session_id": sid, "status": "discarded", "title": "Refund flow"}
    assert sid not in {s["session_id"] for s in svc.list_sessions(include_discarded=True)}


def test_deleting_a_rejected_session_is_allowed():
    svc, sid = _rejected()

    out = svc.delete_session(sid, actor="olafkfreund")

    assert out["status"] == "rejected"
    with pytest.raises(PlanInputError, match="unknown session"):
        svc.delete_session(sid, actor="olafkfreund")  # gone: same as unknown


# ── service: refused statuses ────────────────────────────────────────────────


def test_deleting_an_ingested_session_is_refused():
    svc, sid = _ingested()

    with pytest.raises(PlanInputError, match="ingested"):
        svc.delete_session(sid, actor="olafkfreund")
    # Refused, not silently dropped: still there afterwards.
    assert svc.get(sid).status == "ingested"


def test_deleting_an_emitted_session_is_refused_by_status_name():
    """MUTATION GUARD: the trap in reusing ``completion.TERMINAL_STATUSES``.

    ``emitted`` IS terminal, so a delete gated on ``TERMINAL_STATUSES`` instead
    of ``DELETABLE_STATUSES`` would let this through and erase the record of a
    real GitHub epic. Widen the gate and this must fail (see the plan's
    negative control 1).
    """
    svc, sid = _emitted()

    with pytest.raises(PlanInputError, match="emitted"):
        svc.delete_session(sid, actor="olafkfreund")
    assert svc.get(sid).status == "emitted"


# ── service: tenant + unknown ────────────────────────────────────────────────


def test_deleting_an_unknown_id_is_refused():
    svc = PlanService()
    with pytest.raises(PlanInputError, match="unknown session"):
        svc.delete_session("no-such-session", actor="olafkfreund")


def test_another_tenants_id_fails_with_the_same_message_as_unknown():
    """No 404-vs-409 or message tell — a wrong tenant must not probe ids.

    Compares against the message a genuinely unknown id with the SAME session
    id text produces, since two different ids would trivially differ.
    """
    svc, sid = _discarded(tenant_id="acme")

    # The message a genuinely unknown id with this exact text produces —
    # `_load_from_store`/`get()`'s literal, which `delete_session` also raises.
    with pytest.raises(PlanInputError) as unknown_exc:
        PlanService().delete_session(sid, actor="olafkfreund")
    reference_message = str(unknown_exc.value)

    with pytest.raises(PlanInputError) as tenant_exc:
        svc.delete_session(sid, actor="olafkfreund", tenant_id="globex")

    assert str(tenant_exc.value) == reference_message
    # ...while the owning tenant can still delete it.
    out = svc.delete_session(sid, actor="olafkfreund", tenant_id="acme")
    assert out["session_id"] == sid


# ── service: list_sessions hides discarded ───────────────────────────────────


def test_list_sessions_hides_discarded_by_default():
    svc, sid_discarded = _discarded()
    _, sid_ingested = _ingested(svc)

    visible = {s["session_id"] for s in svc.list_sessions()}
    assert sid_discarded not in visible
    assert sid_ingested in visible

    with_discarded = {s["session_id"] for s in svc.list_sessions(include_discarded=True)}
    assert sid_discarded in with_discarded
    assert sid_ingested in with_discarded


def test_list_sessions_does_not_hide_rejected():
    """Only ``discarded`` is hidden — a rejected plan is still work to fix."""
    svc, sid_rejected = _rejected()

    visible = {s["session_id"] for s in svc.list_sessions()}
    assert sid_rejected in visible


# ── route ─────────────────────────────────────────────────────────────────


def _use_service(monkeypatch, svc: PlanService) -> None:
    monkeypatch.setattr(pp, "SERVICE", svc)


def test_route_delete_returns_200_then_404_on_a_second_delete(monkeypatch):
    svc, sid = _discarded()
    _use_service(monkeypatch, svc)

    body = pp.DeleteBody(actor="olafkfreund")
    out = asyncio.run(pp.delete_session(sid, body, _Request(), db=None))
    assert out["session_id"] == sid

    with pytest.raises(HTTPException) as exc:
        asyncio.run(pp.delete_session(sid, body, _Request(), db=None))
    assert exc.value.status_code == 404


def test_route_delete_of_a_non_deletable_status_is_409(monkeypatch):
    svc, sid = _ingested()
    _use_service(monkeypatch, svc)

    body = pp.DeleteBody(actor="olafkfreund")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(pp.delete_session(sid, body, _Request(), db=None))
    assert exc.value.status_code == 409


def test_route_delete_writes_an_audit_record(monkeypatch):
    """The delete must be audited (#798 intent decision 1).

    The other route tests pass ``db=None``, which ``log_audit_event`` swallows
    inside its SAVEPOINT — so they would pass just as well with no audit call at
    all. This one captures the call instead.
    """
    svc, sid = _discarded()
    _use_service(monkeypatch, svc)
    captured: dict = {}

    async def _capture(db, **kwargs) -> None:  # noqa: ANN001, ARG001
        captured.update(kwargs)

    monkeypatch.setattr(pp, "log_audit_event", _capture)

    body = pp.DeleteBody(actor="olafkfreund", reason="nightly probe cleanup")
    asyncio.run(pp.delete_session(sid, body, _Request(), db=None))

    assert captured["action"] == "plan_session.delete"
    assert captured["resource_type"] == "plan_session"
    assert captured["resource_id"] == sid
    assert captured["details"]["actor"] == "olafkfreund"
    assert captured["details"]["reason"] == "nightly probe cleanup"
    assert captured["details"]["status"] == "discarded"


def test_a_refused_delete_writes_no_audit_record(monkeypatch):
    """No record for something that did not happen."""
    svc, sid = _ingested()
    _use_service(monkeypatch, svc)
    calls: list = []

    async def _capture(db, **kwargs) -> None:  # noqa: ANN001, ARG001
        calls.append(kwargs)

    monkeypatch.setattr(pp, "log_audit_event", _capture)

    with pytest.raises(HTTPException):
        asyncio.run(pp.delete_session(sid, pp.DeleteBody(actor="olafkfreund"), _Request(), db=None))
    assert calls == []
