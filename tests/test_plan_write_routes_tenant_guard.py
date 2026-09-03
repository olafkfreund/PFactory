"""The MUTATING plan routes must honour the tenant guard (#308, review on #703).

`get_session` checked the tenant inline; the write routes shipped without it. So
in multi-tenant mode a caller holding another tenant's session id could rewrite
that plan and re-process it.

`/process` is the sharper case: it was already unguarded, but it only re-ran a
plan. Giving it an update body turned an unguarded read-ish call into an
unguarded WRITE, which is an escalation introduced by this work rather than one
inherited from it.

The guard answers 404, never 403 — a wrong tenant must not be able to use the
status code to learn a session id exists.
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
from plan.annotate.models import AnnotationResult, SuggestedEdit  # noqa: E402
from plan.service import PlanService  # noqa: E402
from server import tenancy  # noqa: E402
from server.routes import plan_pipeline as pp  # noqa: E402

_PLAN = """# Refund API
Add a REST API endpoint to the payments microservice.
## Acceptance Criteria
- User can request a refund through the API
- The endpoint requires a valid JWT and rejects unauthenticated callers
"""


class _Request:
    """Minimal stand-in — `resolve_tenant` reads headers off the request."""

    def __init__(self, tenant: str) -> None:
        self.headers = {"X-Tenant-Id": tenant}


@pytest.fixture
def service(monkeypatch):
    svc = PlanService(persist=False)
    monkeypatch.setattr(pp, "SERVICE", svc)
    # Patch BOTH: `plan_pipeline` holds its own imported name (the guard reads
    # that one) and `resolve_tenant` reads `server.tenancy`'s. Patching only the
    # first left resolve_tenant returning "default" for every caller — the
    # cross-tenant tests then passed without the header mattering at all, which
    # is a test that cannot tell two tenants apart.
    monkeypatch.setattr(pp, "multi_tenant_enabled", lambda: True)
    monkeypatch.setattr(tenancy, "multi_tenant_enabled", lambda: True)
    return svc


@pytest.fixture
def owned(service):
    """A processed session belonging to tenant "acme"."""
    session = service.ingest_text(_PLAN, title="Refund API", tenant_id="acme")
    processed = service.process(session.session_id)
    processed.annotation = AnnotationResult(
        suggestions=[
            SuggestedEdit(
                id="S1",
                suggestion="missing required tag 'owner'",
                replacement="owner: acme-team",
                mode="append_tag",
            )
        ]
    )
    return processed


def test_another_tenant_cannot_rewrite_a_plan_via_process(service, owned):
    before = service.get(owned.session_id).plan.title

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            pp.process(
                owned.session_id,
                _Request("intruder"),
                updates=pp.PlanUpdateBody(title="OWNED"),
            )
        )

    assert caught.value.status_code == 404, "404, never 403 — do not leak existence"
    assert service.get(owned.session_id).plan.title == before, "the plan must be untouched"


def test_another_tenant_cannot_apply_suggestions(service, owned):
    before = service.get(owned.session_id).plan.description

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            pp.apply_suggestions(
                owned.session_id,
                pp.ApplySuggestionsBody(accepted=[pp.AcceptedSuggestion(id="S1")]),
                _Request("intruder"),
            )
        )

    assert caught.value.status_code == 404
    assert service.get(owned.session_id).plan.description == before


def test_the_owning_tenant_is_unaffected(service, owned):
    result = asyncio.run(
        pp.apply_suggestions(
            owned.session_id,
            pp.ApplySuggestionsBody(accepted=[pp.AcceptedSuggestion(id="S1")], reprocess=False),
            _Request("acme"),
        )
    )

    assert result["applied"] == [
        {
            "id": "S1",
            "mode": "append_tag",
            "suggestion": "missing required tag 'owner'",
            "source": "",
        }
    ]
    assert "owner: acme-team" in service.get(owned.session_id).plan.description


def test_single_tenant_mode_is_unchanged(service, owned, monkeypatch):
    """The guard must be inert when multi-tenancy is off, or it breaks everyone."""
    monkeypatch.setattr(pp, "multi_tenant_enabled", lambda: False)
    monkeypatch.setattr(tenancy, "multi_tenant_enabled", lambda: False)

    result = asyncio.run(
        pp.apply_suggestions(
            owned.session_id,
            pp.ApplySuggestionsBody(accepted=[pp.AcceptedSuggestion(id="S1")], reprocess=False),
            _Request("anyone-at-all"),
        )
    )

    assert result["status"] in {"ingested", "processed"}


def test_the_header_is_what_decides(service, owned):
    """Guards against the trap this suite already fell into once.

    If `resolve_tenant` ignores the request (e.g. only one of the two
    `multi_tenant_enabled` names is patched), every caller resolves to the same
    tenant and the cross-tenant tests above pass without testing anything. This
    asserts the two tenants actually resolve differently.
    """
    assert tenancy.resolve_tenant(_Request("acme")) == "acme"
    assert tenancy.resolve_tenant(_Request("intruder")) == "intruder"
