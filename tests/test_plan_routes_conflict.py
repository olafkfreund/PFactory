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
        "emit": lambda: pp.emit(sid, pp.EmitBody(repo="o/r", dry_run=False), None, None),
        "emit_contract": lambda: pp.emit_contract(sid, pp.EmitContractBody(dry_run=False)),
        "approve": lambda: pp.approve(sid, pp.ApproveBody(approver="a")),
        "discard": lambda: pp.discard(sid, pp.DiscardBody(actor="a", reason="r")),
        "reject": lambda: pp.reject(sid, pp.RejectBody(approver="a", feedback="f")),
        "process": lambda: pp.process(sid, _Request()),
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
