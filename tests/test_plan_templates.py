"""Tests for the plan.templates loader + gcp-project exemplar + enforcement (#26).

Note: filed as ``test_plan_templates.py`` (not ``test_templates.py``) because a
pre-existing ``tests/test_templates.py`` already covers the unrelated
``templates_pkg.engine`` test-template engine. This file covers the planning
factory's Backstage-compatible ``plan.templates`` loader and embedded policy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.templates import (  # noqa: E402
    build_context,
    enforce,
    load_template,
    load_templates,
    select_template,
)

_GCP_TEMPLATE = (
    _BACKEND / "plan" / "templates" / "gcp-project" / "template.yaml"
)


def _plan(
    *,
    title: str,
    description: str = "",
    raw_text: str | None = None,
    criteria: list[str] | None = None,
    plan_type: str | None = None,
) -> NormalizedPlan:
    """Build a NormalizedPlan directly for test scenarios."""
    return NormalizedPlan(
        plan_id="001-test",
        title=title,
        description=description,
        source_format="markdown",
        plan_type=plan_type,
        raw_text=raw_text,
        criteria=[
            Criterion(id=f"AC#{i + 1}", text=text)
            for i, text in enumerate(criteria or [])
        ],
    )


# ── load_template ──────────────────────────────────────────────────────────


def test_load_template_parses_policy_and_kind() -> None:
    template = load_template(_GCP_TEMPLATE)
    assert template.kind == "Template"
    assert template.metadata.name == "gcp-project"
    assert template.policy.required_tags  # populated
    assert "cost-center" in template.policy.required_tags
    assert "europe-west1" in template.policy.allowed_regions
    assert "roles/viewer" in template.policy.required_iam
    assert template.policy.rules  # a couple of rules present


# ── load_templates ─────────────────────────────────────────────────────────


def test_load_templates_discovers_gcp_project() -> None:
    templates = load_templates()
    assert "gcp-project" in templates
    assert templates["gcp-project"].kind == "Template"


# ── select_template ────────────────────────────────────────────────────────


def test_select_template_matches_gcp_plan() -> None:
    plan = _plan(
        title="Create a new GCP project for billing",
        description="Set up a Google Cloud project skeleton.",
        plan_type="gcp",
    )
    selected = select_template(plan)
    assert selected is not None
    assert selected.metadata.name == "gcp-project"


def test_select_template_returns_none_for_unrelated_plan() -> None:
    plan = _plan(
        title="Write a haiku about the weather",
        description="A short poem about rain and sun.",
    )
    assert select_template(plan) is None


# ── enforce ────────────────────────────────────────────────────────────────


def test_enforce_flags_noncompliant_plan() -> None:
    # GCP plan with a disallowed region and no required tags/IAM.
    plan = _plan(
        title="New GCP project in asia-southeast1",
        description="Provision a Google Cloud project skeleton.",
        plan_type="gcp",
        raw_text="Deploy resources to asia-southeast1.",
    )
    findings = enforce(plan)
    assert findings, "expected policy violations for a non-compliant plan"
    titles = " ".join(f.title for f in findings)
    assert "asia-southeast1" in titles  # disallowed region flagged
    assert "cost-center" in titles  # missing required tag flagged


def test_enforce_passes_compliant_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan(
        title="New GCP project (golden path)",
        description="Provision a compliant Google Cloud project skeleton.",
        plan_type="gcp",
    )

    # A fully compliant context: all required tags, an allowed region, the
    # required IAM role. Patch build_context so the assertion is unambiguous.
    def _compliant_context(_plan: NormalizedPlan) -> dict:
        return {
            "tags": ["cost-center", "owner", "environment"],
            "region": "europe-west1",
            "iam": ["roles/viewer"],
            "text": _plan.title,
        }

    monkeypatch.setattr(
        "plan.templates.loader.build_context", _compliant_context
    )

    assert enforce(plan) == []


def test_enforce_returns_empty_when_no_template_matches() -> None:
    plan = _plan(title="Write a haiku", description="poetry only")
    assert enforce(plan) == []


# ── build_context ──────────────────────────────────────────────────────────


def test_build_context_extracts_region_and_iam() -> None:
    plan = _plan(
        title="GCP project",
        raw_text="Deploy to europe-west4 and grant roles/viewer.",
    )
    context = build_context(plan)
    assert context["region"] == "europe-west4"
    assert "roles/viewer" in context["iam"]


def test_build_context_is_defensive_on_empty_plan() -> None:
    plan = _plan(title="empty")
    context = build_context(plan)
    assert context["region"] is None
    assert context["iam"] == []
    assert isinstance(context["tags"], list)
