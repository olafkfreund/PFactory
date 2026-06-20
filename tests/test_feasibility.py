"""Tests for the feasibility engine — cost / access (Phase C).

The deterministic logic (resource extraction, static-fallback pricing, IAM
action-mapping, the orchestrator) is exercised without any live cloud. Live AWS
pricing/IAM paths are guarded in the modules and only run with real credentials
(mark such tests with ``live_cloud``).

RFC-0014 removed the dev-day effort assessor; the scorer's difficulty/risk/
autonomy verdict (test_task_scorer.py) replaces it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.feasibility.access import required_actions, verify_access  # noqa: E402
from plan.feasibility.cost import estimate_cost, extract_resources  # noqa: E402
from plan.feasibility.run import assess_feasibility  # noqa: E402
from plan.models import Criterion, Enrichment, NormalizedPlan  # noqa: E402


def _plan(text: str, *, infra=None) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-x",
        title="Orders platform",
        description=text,
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text=text)],
        enrichment=Enrichment(infra=infra or []),
        raw_text=text,
    )


def _epic(children) -> EpicPlan:
    return EpicPlan(plan_id="001-x", epic_title="Build it", children=children)


# ── cost ─────────────────────────────────────────────────────────────────


def test_extract_resources_from_text_and_enrichment():
    infra = [
        {
            "adapter": "aws",
            "resources": {"instance_types": {"m5.large": 3}, "regions": ["eu-west-2"]},
        }
    ]
    plan = _plan("Multi-region EKS with RDS PostgreSQL and Redis", infra=infra)
    lines = extract_resources(plan)
    keys = {ln.key() for ln in lines}
    assert "aws:eks:cluster" in keys
    assert "aws:rds:db.r6g.large" in keys
    assert "aws:elasticache:cache.r6g.large" in keys
    assert "aws:ec2:m5.large" in keys  # from live enrichment


def test_estimate_cost_static_fallback_always_prices():
    plan = _plan("Deploy an EKS cluster with an ALB")
    # No pricing clients → everything falls back to the static book.
    est = estimate_cost(plan, clients=[])
    assert est.monthly_usd > 0
    assert est.confidence == "low"
    assert "static-fallback" in est.source


def test_estimate_cost_uses_real_client_when_available():
    plan = _plan(
        "EC2 fleet of m5.large",
        infra=[
            {
                "adapter": "aws",
                "resources": {"instance_types": {"m5.large": 2}, "regions": ["us-east-1"]},
            }
        ],
    )

    class _FakeAws:
        provider = "aws"

        def monthly_usd(self, line):
            return 70.0 if line.service == "ec2" else None

    est = estimate_cost(plan, clients=[_FakeAws()])
    assert est.monthly_usd > 0
    # at least the ec2 line was priced for real → not all-static
    assert est.confidence in {"medium", "high"}
    assert "aws-price-list" in est.source


# ── access ───────────────────────────────────────────────────────────────


def test_required_actions_maps_services():
    plan = _plan("Provision EKS and an S3 bucket")
    actions = dict(required_actions(plan))  # {action: provider}... actually (provider, action)
    pairs = required_actions(plan)
    assert ("aws", "eks:CreateCluster") in pairs
    assert ("aws", "s3:CreateBucket") in pairs


def test_verify_access_flags_denied_actions():
    plan = _plan("Provision an EKS cluster")

    def sim(actions):
        return {a: ("allowed" if a == "eks:CreateCluster" else "implicitDeny") for a in actions}

    reqs, findings = verify_access(plan, aws_simulator=sim)
    denied = [r for r in reqs if r.granted is False]
    assert denied  # ec2:RunInstances / iam:CreateRole denied
    assert any(f.severity == "high" and f.source == "feasibility-access" for f in findings)
    # every change-proposing finding is cited
    assert all(f.citations for f in findings if f.severity == "high")


def test_verify_access_unverified_when_no_simulator():
    plan = _plan("Provision an EKS cluster")
    # aws_simulator returns None → unverified advisory (granted None), never raises.
    reqs, findings = verify_access(plan, aws_simulator=lambda a: None)
    assert all(r.granted is None for r in reqs if r.provider == "aws")
    assert any("not verified" in f.title.lower() for f in findings)


# ── orchestrator ─────────────────────────────────────────────────────────


def test_assess_feasibility_bundles_everything():
    plan = _plan("Multi-region EKS with RDS and Redis")
    epic = _epic([ChildIssue(key="C1", title="cluster", complexity="complex")])
    result = assess_feasibility(plan, epic)
    assert result.cost is not None and result.cost.monthly_usd > 0
    sources = {f.source for f in result.findings}
    assert "feasibility-cost" in sources
    # RFC-0014: no effort assessor — feasibility-effort findings are gone.
    assert "feasibility-effort" not in sources
