"""RFC-0007 (#86 PR-f): POST /{session_id}/access/approve route.

The curation logic is covered by test_approve_access.py; this verifies the
FastAPI layer delegates to SERVICE.approve_access and maps errors to HTTP.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from server.routes import plan_pipeline as pp  # noqa: E402


def test_route_delegates_to_service(monkeypatch):
    seen = {}

    def fake_approve(session_id, resource, *, approved_by, scope, approved_at=None):
        seen.update(
            session_id=session_id,
            resource=resource,
            approved_by=approved_by,
            scope=scope,
        )
        return {"ok": True, "resource": resource, "state": "curated"}

    monkeypatch.setattr(pp.SERVICE, "approve_access", fake_approve)
    body = pp.ApproveAccessBody(resource="web", approved_by="olaf", scope="staging")
    out = asyncio.run(pp.approve_access("s1", body))

    assert out == {"ok": True, "resource": "web", "state": "curated"}
    assert seen == {
        "session_id": "s1",
        "resource": "web",
        "approved_by": "olaf",
        "scope": "staging",
    }


def test_route_maps_service_error_to_400(monkeypatch):
    def boom(*a, **k):
        raise pp.PlanServiceError("emit a dry-run contract first")

    monkeypatch.setattr(pp.SERVICE, "approve_access", boom)
    body = pp.ApproveAccessBody(resource="x", approved_by="o", scope="s")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(pp.approve_access("s1", body))
    assert ei.value.status_code == 400
