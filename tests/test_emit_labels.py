"""Tests for the emit taxonomy — labels + pfactory:meta block (Phase H, #6)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import (  # noqa: E402
    AccessRequirement,
    ChildIssue,
    CostEstimate,
    EffortEstimate,
    EpicPlan,
)
from plan.emit.labels import (  # noqa: E402
    pfactory_meta_block,
    priority_for,
    taxonomy_labels,
)
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.review.models import Citation, Finding, LensScore, PlanReview  # noqa: E402


def _plan(plan_type="infra-change") -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-orders",
        title="Orders platform",
        source_format="markdown",
        target_kind="software",
        plan_type=plan_type,
        criteria=[Criterion(id="AC#1", text="Provision EKS with Terraform")],
        raw_text="Provision a multi-region EKS cluster with Terraform.",
    )


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="001-orders",
        epic_title="Build orders platform",
        children=[
            ChildIssue(key="C1", title="Provision EKS", kind="infra"),
            ChildIssue(key="C2", title="Add tests", kind="testing"),
        ],
        cost_estimate=CostEstimate(monthly_usd=2492.58, confidence="medium", source="aws-price-list"),
        effort_estimate=EffortEstimate(story_points=39, duration_days_low=15.6, duration_days_high=39.0),
        access_requirements=[AccessRequirement(provider="aws", action="eks:CreateCluster", granted=True)],
    )


def _review(*, blocking=False, passed=True) -> PlanReview:
    findings = [Finding(
        title="x", severity="high", source="security", blocking=blocking,
        citations=[Citation(why="auth needed", uri="https://owasp.org", source="owasp")],
    )]
    r = PlanReview(plan_id="001-orders", lenses=[LensScore(lens="security", score=0.9, findings=findings)])
    r.gates_passed = passed
    return r


def test_taxonomy_labels_core_set():
    labels = taxonomy_labels(_plan(), _epic(), _review())
    assert "pfactory" in labels
    assert "handoff:aifactory" in labels
    assert "type:infra" in labels                 # infra-change → category infrastructure → type:infra
    assert "plan-type:infra-change" in labels
    assert any(label.startswith("priority:") for label in labels)
    assert "handoff:tfactory" in labels           # has a testing child
    assert "sev:high" in labels                    # worst finding severity


def test_priority_escalates_with_findings():
    assert priority_for(_review(blocking=True)) == "p0"
    assert priority_for(_review(passed=False)) == "p1"
    assert priority_for(_review(passed=True)) == "p2"


def test_meta_block_is_machine_readable():
    block = pfactory_meta_block(_plan(), _epic(), _review())
    assert block.startswith("<!-- pfactory:meta")
    assert block.rstrip().endswith("-->")
    assert "plan_id: 001-orders" in block
    assert "cost_monthly_usd: 2492.58" in block
    assert "effort_points: 39" in block
    assert "access_verified: true" in block
    assert "taxonomy: v1" in block
    assert "citations:" in block and "owasp.org" in block


def test_labels_deduped():
    labels = taxonomy_labels(_plan(), _epic(), _review())
    assert len(labels) == len(set(labels))
