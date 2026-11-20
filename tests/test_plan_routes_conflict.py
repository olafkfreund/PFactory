"""The plan routes answer 409 for the two cross-replica conflicts (#758).

``EmitInProgressError`` (another replica holds the emit lease, or it cannot be
confirmed) and ``StaleSessionError`` (another replica changed the session) are
``PlanServiceError`` subclasses, so without their own ``except`` arm they fall
into the generic 400/404 and tell the caller the request was wrong rather than
"retry". SERVICE is replaced by a stub that raises, so no store is needed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402

from server.routes import plan_pipeline as pp  # noqa: E402


class _Request:
    headers: ClassVar[dict[str, str]] = {}


class _Session:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _RaisingService:
    """Every SERVICE method a route calls raises the configured error."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __getattr__(self, name: str):
        def _raise(*_args, **_kwargs):
            raise self._exc

        return _raise


def _call(route: str):
    sid = "PLAN-1"
    calls = {
        "emit": lambda: pp.emit(sid, pp.EmitBody(repo="o/r", dry_run=False), None, _Session()),
        "emit_contract": lambda: pp.emit_contract(
            sid, pp.EmitContractBody(dry_run=False), _Request(), None
        ),
        "approve": lambda: pp.approve(sid, pp.ApproveBody(approver="a"), _Request(), None),
        "discard": lambda: pp.discard(sid, pp.DiscardBody(actor="a", reason="r"), _Request(), None),
        "reject": lambda: pp.reject(
            sid, pp.RejectBody(approver="a", feedback="f"), _Request(), None
        ),
        "process": lambda: pp.process(sid, _Request(), None),
    }
    return asyncio.run(calls[route]())


def _status(monkeypatch, route: str, exc: Exception) -> int:
    monkeypatch.setattr(pp, "SERVICE", _RaisingService(exc))
    with pytest.raises(HTTPException) as info:
        _call(route)
    assert info.value.detail  # the curated message reaches the caller
    return info.value.status_code


@pytest.mark.parametrize("route", ["emit", "emit_contract"])
@pytest.mark.parametrize("error", ["EmitInProgressError", "StaleSessionError"])
def test_emit_conflicts_are_409(monkeypatch, route, error):
    exc = getattr(pp, error)("another replica is on it")
    assert _status(monkeypatch, route, exc) == 409


def test_emit_releases_db_before_service_runs(monkeypatch):
    db = _Session()
    observed = []

    class _Service:
        def emit(self, *_args, **_kwargs):
            observed.append(db.closed)
            return {"ok": True}

    monkeypatch.setattr(pp, "SERVICE", _Service())
    monkeypatch.setattr(pp, "_session_dict", lambda session: session)

    async def no_guard(*_args):
        pass

    monkeypatch.setattr(pp, "_guard_session", no_guard)

    result = asyncio.run(pp.emit("PLAN-1", pp.EmitBody(repo="o/r"), None, db))
    assert result == {"ok": True}
    assert observed == [True]


@pytest.mark.parametrize("route", ["approve", "discard", "reject", "process"])
def test_stale_write_is_409(monkeypatch, route):
    exc = pp.StaleSessionError("changed by another replica; reload and retry")
    assert _status(monkeypatch, route, exc) == 409


@pytest.mark.parametrize(
    ("route", "status"),
    [("emit", 400), ("emit_contract", 400), ("approve", 400), ("discard", 400), ("process", 404)],
)
def test_plain_service_error_keeps_its_status(monkeypatch, route, status):
    assert _status(monkeypatch, route, pp.PlanServiceError("no such session")) == status


@pytest.mark.parametrize(
    ("auth_kind", "dry_run", "allowed"),
    [
        ("user", False, True),
        ("shared_token", False, False),
        ("unknown", False, False),
        ("shared_token", True, True),
        ("unknown", True, True),
    ],
)
def test_mcp_emit_requires_user_approval(monkeypatch, auth_kind, dry_run, allowed):
    class Service:
        def get(self, _session_id):
            approval = SimpleNamespace(approved=True, valid=True, auth_kind=auth_kind)
            return SimpleNamespace(review=SimpleNamespace(human_approval=approval), plan=None)

        def emit_contract(self, *_args, **_kwargs):
            return {"ok": True}

    async def no_guard(*_args):
        pass

    monkeypatch.setattr(pp, "SERVICE", Service())
    monkeypatch.setattr(pp, "_guard_session", no_guard)
    monkeypatch.setattr(pp, "_session_dict", lambda session: session)
    monkeypatch.setattr(pp, "approval_is_valid", lambda review, _plan: review.human_approval.valid)
    request = SimpleNamespace(headers={"X-PFactory-Channel": "mcp"})
    call = pp.emit_contract("PLAN-1", pp.EmitContractBody(dry_run=dry_run), request, None)
    if allowed:
        assert asyncio.run(call) == {"ok": True}
    else:
        with pytest.raises(HTTPException) as info:
            asyncio.run(call)
        assert info.value.status_code == 409


@pytest.mark.parametrize(
    ("user", "expected_kind"),
    [({"id": "user-123"}, "user"), ({"id": "default", "is_service": True}, "shared_token")],
)
def test_approve_uses_authenticated_principal_and_commits_audit(monkeypatch, user, expected_kind):
    observed = {}

    class Service:
        def approve(self, *_args, **kwargs):
            observed.update(kwargs)
            return {"ok": True}

    class DB:
        async def commit(self):
            observed["committed"] = True

    async def no_guard(*_args):
        pass

    async def audit(*_args, **kwargs):
        observed["audit"] = kwargs

    monkeypatch.setattr(pp, "SERVICE", Service())
    monkeypatch.setattr(pp, "_guard_session", no_guard)
    monkeypatch.setattr(pp, "_session_dict", lambda session: session)
    monkeypatch.setattr(pp, "log_audit_event", audit)
    request = SimpleNamespace(state=SimpleNamespace(user=user), client=None)
    result = asyncio.run(pp.approve("PLAN-1", pp.ApproveBody(approver="label"), request, DB()))
    assert result == {"ok": True}
    assert observed["user_id"] == ("user-123" if expected_kind == "user" else None)
    assert observed["auth_kind"] == expected_kind
    assert observed["audit"]["action"] == "plan_session.approve"
    assert observed["committed"] is True
