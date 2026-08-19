"""Tests for document annotation — honour the doc + suggest cited edits (Phase D)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.annotate import annotate_plan  # noqa: E402
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.review.models import (  # noqa: E402
    Citation,
    Finding,
    LensScore,
    PlanReview,
)

_ORIGINAL = """# Orders Platform SOW

## Networking
All services run behind an internal ALB.

## Data
RDS PostgreSQL Multi-AZ in the primary region.
"""


def _plan() -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-x",
        title="Orders Platform SOW",
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text="RDS PostgreSQL Multi-AZ")],
        raw_text=_ORIGINAL,
    )


def _review(findings) -> PlanReview:
    return PlanReview(plan_id="001-x", lenses=[LensScore(lens="security", findings=findings)])


def test_change_proposing_findings_become_anchored_cited_suggestions():
    finding = Finding(
        title="Principal cannot rds:CreateDBInstance",
        detail="Grant the action before handoff.",
        severity="high",
        source="feasibility-access",
        citations=[Citation(why="The plan needs this action.", uri="https://docs.aws.amazon.com/iam", title="IAM", source="aws-iam")],
    )
    result = annotate_plan(_plan(), _review([finding]))
    assert len(result.suggestions) == 1
    s = result.suggestions[0]
    # Anchored to the RDS line in the original.
    assert s.anchor_line > 0
    assert "rds" in s.original_excerpt.lower()
    assert s.why and s.citation and s.citation.uri
    assert s.severity == "high"


def test_info_findings_are_not_suggestions():
    info = Finding(title="Estimated cost ≈ $100/mo", severity="info", source="feasibility-cost")
    result = annotate_plan(_plan(), _review([info]))
    assert result.suggestions == []
    # No suggestions → improved draft is the original verbatim.
    assert result.improved_markdown == _ORIGINAL


def test_improved_draft_preserves_original_and_appends_cited_section():
    finding = Finding(
        title="No authentication/authorization criteria",
        detail="Confirm access control is in scope.",
        severity="medium",
        source="security",
        citations=[Citation(why="A networked service needs auth.", uri="https://owasp.org", title="OWASP", source="owasp")],
    )
    result = annotate_plan(_plan(), _review([finding]))
    md = result.improved_markdown
    assert md.startswith("# Orders Platform SOW")          # original preserved verbatim
    assert "PFactory review — suggested edits" in md        # cited section appended
    # The whole rendered Source line, not "owasp.org" loose in the document: a
    # substring check passes even if the URI is dropped from the link and only
    # echoed in the prose, which is exactly the rendering bug worth catching.
    assert "- **Source:** [OWASP](https://owasp.org)" in md
    assert result.change_log and result.change_log[0]["citation_uri"] == "https://owasp.org"


def test_no_review_yields_no_suggestions():
    result = annotate_plan(_plan(), None)
    assert result.suggestions == []
    assert result.original_preserved is True
