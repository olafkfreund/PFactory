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
from plan.feasibility.access import (  # noqa: E402
    _mentions_provider,
    required_actions,
    verify_access,
)
from plan.feasibility.cost import estimate_cost, extract_resources  # noqa: E402
from plan.feasibility.run import assess_feasibility  # noqa: E402
from plan.models import Criterion, Enrichment, NormalizedPlan  # noqa: E402
from plan.recon.models import RepoMap  # noqa: E402


def _plan(text: str, *, infra=None, repo_map=None) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="001-x",
        title="Orders platform",
        description=text,
        source_format="markdown",
        target_kind="software",
        criteria=[Criterion(id="AC#1", text=text)],
        enrichment=Enrichment(infra=infra or []),
        raw_text=text,
        repo_map=repo_map,
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


def test_required_actions_suppresses_aws_for_kubectl_plan_with_no_aws_mention():
    # The reported case: a plan naming Kubernetes and postgres (generic nouns,
    # not explicit AWS mentions), deployed via kubectl, no AWS term anywhere.
    plan = _plan(
        "Remediate the Kubernetes deployment and its postgres database.",
        repo_map=RepoMap(available=True, deploy_system="kubectl"),
    )
    pairs = required_actions(plan)
    assert not [p for p in pairs if p[0] == "aws"]


_GENERIC_K8S_PG_TEXT = "Deploy a Kubernetes cluster backed by a postgres database."


def test_required_actions_fail_open_no_repo_map():
    # Generic nouns only (no explicit AWS term) — repo_map=None is the clause
    # under test; it alone must keep today's behaviour.
    plan = _plan(_GENERIC_K8S_PG_TEXT)  # repo_map=None (default)
    pairs = required_actions(plan)
    assert ("aws", "eks:CreateCluster") in pairs
    assert ("aws", "rds:CreateDBInstance") in pairs


def test_required_actions_fail_open_repo_map_unavailable():
    plan = _plan(
        _GENERIC_K8S_PG_TEXT,
        repo_map=RepoMap(available=False, deploy_system="kubectl"),
    )
    pairs = required_actions(plan)
    assert ("aws", "eks:CreateCluster") in pairs
    assert ("aws", "rds:CreateDBInstance") in pairs


def test_required_actions_fail_open_unrecognised_deploy_system():
    plan = _plan(
        _GENERIC_K8S_PG_TEXT,
        repo_map=RepoMap(available=True, deploy_system="terraform"),
    )
    pairs = required_actions(plan)
    assert ("aws", "eks:CreateCluster") in pairs
    assert ("aws", "rds:CreateDBInstance") in pairs


def test_required_actions_fail_open_explicit_mention():
    plan = _plan(
        "Provision an RDS instance.",
        repo_map=RepoMap(available=True, deploy_system="kubectl"),
    )
    pairs = required_actions(plan)
    assert ("aws", "rds:CreateDBInstance") in pairs


def test_required_actions_azure_and_gcp_hints_unaffected_by_the_aws_guard():
    # aks/gke are themselves the explicit-mention terms for their own
    # providers (see _PROVIDER_MENTIONS), so naming them is never
    # suppressible under a kubectl deploy — same as before this change.
    azure_plan = _plan(
        "Provision an AKS cluster.",
        repo_map=RepoMap(available=True, deploy_system="kubectl"),
    )
    assert ("azure", "Microsoft.ContainerService/managedClusters/write") in required_actions(
        azure_plan
    )

    gcp_plan = _plan(
        "Provision a GKE cluster.",
        repo_map=RepoMap(available=True, deploy_system="kubectl"),
    )
    assert ("gcp", "container.clusters.create") in required_actions(gcp_plan)


def test_required_actions_dedup_across_multiple_hints():
    # Two distinct hints (kubernetes->eks, postgres->rds) both fire for the
    # same provider; every (provider, action) pair still appears exactly
    # once — the `seen` dedup survived the step-3 restructuring.
    plan = _plan("Deploy Kubernetes with a postgres database")
    pairs = required_actions(plan)
    assert len(pairs) == len(set(pairs))
    assert pairs.count(("aws", "eks:CreateCluster")) == 1
    assert pairs.count(("aws", "rds:CreateDBInstance")) == 1


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


def test_mentions_provider_excludes_generic_nouns():
    # "kubernetes"/"postgres" are the generic nouns _ACTION_HINTS matches —
    # they must not count as an explicit mention of AWS.
    assert not _mentions_provider("Deploy Kubernetes with a postgres database", "aws")


def test_mentions_provider_matches_explicit_aws_terms():
    for term in ("aws", "eks", "rds", "s3", "ec2", "iam", "elasticache"):
        assert _mentions_provider(f"uses {term} here", "aws"), term


def test_mentions_provider_matches_explicit_azure_and_gcp_terms():
    for term in ("aks", "azure"):
        assert _mentions_provider(f"uses {term} here", "azure"), term
    for term in ("gke", "gcp"):
        assert _mentions_provider(f"uses {term} here", "gcp"), term
    # negative case: text naming neither provider must be False — only
    # possible while the azure/gcp patterns exist (fail-open would make a
    # missing pattern read as True, hiding a deleted entry).
    assert not _mentions_provider("deploy with kubectl to the cluster", "azure")
    assert not _mentions_provider("deploy with kubectl to the cluster", "gcp")


def test_mentions_provider_unknown_provider_fails_open():
    # No entry in the table at all — treated as mentioned, so a future
    # provider never gets silently suppressed for want of a pattern.
    assert _mentions_provider("deploy somewhere", "oracle")


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


# ── the repo's own provider evidence overrides deploy_system (#839 review) ───
# Found by independent review: the first cut keyed on deploy_system alone and
# went silent on repos that plainly DO use the provider. Both cases below are
# suppression on ABSENCE of evidence, which this gate's governing rule forbids.
# Worth stating what is lost: required_actions returning [] makes verify_access
# return early, so no IAM simulation runs and the severity="high" "Principal
# cannot rds:CreateDBInstance" finding never fires. A real explicitDeny becomes
# silence that surfaces at handoff -- not the "spurious low/soft line" the spec
# weighed this against.

_GENERIC_AWS_TRIGGER = "Add a postgres database for orders."


def test_required_actions_not_suppressed_when_recon_named_the_provider():
    """deploy_system "none" means recon RECOGNISED nothing, not that nothing is
    provisioned.

    probe_iac knows only terraform/helm/kubernetes, so a CDK / SAM / serverless
    repo -- exactly the files detect_cloud_providers reads to set
    cloud_providers=["aws"] -- lands on "none", which is in
    _NON_CLOUD_DEPLOY_SYSTEMS.
    """
    plan = _plan(
        _GENERIC_AWS_TRIGGER,
        repo_map=RepoMap(available=True, deploy_system="none", cloud_providers=["aws"]),
    )
    assert ("aws", "rds:CreateDBInstance") in required_actions(plan)


def test_required_actions_not_suppressed_when_the_repo_carries_provisioning_iac():
    """deploy_system precedence is argocd > helm > terraform > kubectl.

    So the common shape -- Terraform provisions RDS, Helm deploys the app --
    resolves to "helm" while iac and deploy_manifests both say terraform.
    """
    plan = _plan(
        _GENERIC_AWS_TRIGGER,
        repo_map=RepoMap(
            available=True,
            deploy_system="helm",
            iac=["terraform", "helm"],
            deploy_manifests={"terraform_files": ["infra/rds.tf"]},
        ),
    )
    assert ("aws", "rds:CreateDBInstance") in required_actions(plan)


def test_required_actions_still_suppressed_without_provider_evidence():
    """The reported case must stay fixed: no cloud_providers, no provisioning
    IaC, kubectl, and no AWS term in the text."""
    plan = _plan(
        "Deploy Kubernetes with a postgres database.",
        repo_map=RepoMap(available=True, deploy_system="kubectl"),
    )
    assert not [a for prov, a in required_actions(plan) if prov == "aws"]
