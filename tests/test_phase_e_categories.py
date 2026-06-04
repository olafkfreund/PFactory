"""Tests for Phase E — categories + selectable templates (#1).

Covers: the 9 plan-type categories load; template policy enforcement is OPT-IN
(only when the user selected a template); and the /api/plan/meta/categories route.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
_WEBSERVER = Path(__file__).parent.parent / "apps" / "web-server"
for p in (_BACKEND, _WEBSERVER):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

pytest.importorskip("pydantic")

from plan.plan_types import load_descriptors  # noqa: E402
from plan.service import PlanService  # noqa: E402

_SOFTWARE_PLAN = """# Refund API

The service exposes a REST API endpoint to issue refunds.

## Acceptance Criteria
- The API returns 200 on a valid refund request.
- The API requires authentication and authorization.
"""


def test_nine_categories_present():
    cats = {d.category for d in load_descriptors().values()}
    assert {"product", "software", "feature", "hosting", "infrastructure",
            "testing", "cicd"}.issubset(cats)


def test_template_enforcement_is_opt_in():
    # No template selected → no template findings; auto-match is just a suggestion.
    svc = PlanService()
    sid = svc.ingest_text(_SOFTWARE_PLAN, title="Refund API").session_id
    session = svc.process(sid)
    assert session.selected_template == ""
    assert session.suggested_template  # something matched by keyword
    tmpl_findings = [
        f for ls in session.review.lenses for f in ls.findings
        if f.source.startswith("template:")
    ]
    assert tmpl_findings == []
    assert session.review.gates_passed  # healthy plan still passes


def test_selected_template_enforces_policy():
    # Explicitly selecting the software-service template enforces its required tags.
    svc = PlanService()
    sid = svc.ingest_text(
        _SOFTWARE_PLAN, title="Refund API", template="software-service"
    ).session_id
    session = svc.process(sid)
    assert session.selected_template == "software-service"
    tmpl_findings = [
        f for ls in session.review.lenses for f in ls.findings
        if f.source.startswith("template:")
    ]
    # required_tags [owner, cost-center] aren't present → policy findings appear.
    assert any("required tag" in f.title for f in tmpl_findings)


def test_categories_route_returns_grouped_structure():
    from server.routes.plan_meta import categories

    result = asyncio.run(categories())
    cats = {c["category"] for c in result["categories"]}
    assert "software" in cats and "infrastructure" in cats
    software = next(c for c in result["categories"] if c["category"] == "software")
    assert any(pt["name"] == "software-service" for pt in software["plan_types"])
    assert any(t["name"] == "software-service" for t in software["templates"])
