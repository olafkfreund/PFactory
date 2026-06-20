"""Tests for RFC-0013 deployment-aware planning (PFactory side, #188/#189/#190).

Covers the three new modules:
  * ``plan.recon.ci_probe`` — CI + deploy-manifest discovery (#188);
  * ``plan.recon.dora_client`` — honest DORA context (#189);
  * ``plan.feasibility.deployment`` — the derived ``deployment`` block (#190),
    plus the readiness check + AC injection.

Every emitted block is validated against the authoritative vendored schema
(``$defs.deployment``) so the producer can't drift from the contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
jsonschema = pytest.importorskip("jsonschema")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.feasibility.deployment import (  # noqa: E402
    assess_deployment_readiness,
    deployment_acs,
    deployment_findings,
    inject_deployment_acs,
)
from plan.models import NormalizedPlan  # noqa: E402
from plan.recon.ci_probe import infer_environments, probe_ci, probe_deploy  # noqa: E402
from plan.recon.dora_client import dora_context  # noqa: E402
from plan.recon.iac_probe import probe_iac  # noqa: E402
from plan.recon.models import RepoMap  # noqa: E402
from plan.review.readiness.checks import _REGISTRY, ReadinessContext  # noqa: E402

_SCHEMA_PATH = _BACKEND / "plan" / "emit" / "contracts" / "task-contract.schema.json"


def _validate_block(block: dict) -> None:
    """Validate a deployment block against the vendored $defs.deployment."""
    schema = json.loads(_SCHEMA_PATH.read_text())
    dep_schema = {"$defs": schema["$defs"], **schema["$defs"]["deployment"]}
    jsonschema.validate(block, dep_schema)


def _plan(rm: RepoMap | None = None) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="p1",
        title="Add metrics endpoint",
        source_format="markdown",
        repo_map=rm,
        change_mode="modify" if rm else None,
    )


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="p1",
        epic_title="Add metrics",
        children=[
            ChildIssue(key="c1", title="impl", kind="feature", acceptance_criteria=["Returns 200"])
        ],
    )


# ── #188: CI + deploy-manifest discovery ────────────────────────────────────


def test_probe_ci_detects_github_actions(tmp_path: Path) -> None:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "deploy.yml").write_text("name: deploy\non: push\n")
    ci = probe_ci(tmp_path)
    assert ci["system"] == "github-actions"
    assert ci["paths"] == [".github/workflows/deploy.yml"]


def test_probe_ci_gitlab_and_azure(tmp_path: Path) -> None:
    (tmp_path / ".gitlab-ci.yml").write_text("stages: [test]\n")
    assert probe_ci(tmp_path)["system"] == "gitlab-ci"

    other = tmp_path / "azure"
    other.mkdir()
    (other / "azure-pipelines.yml").write_text("trigger: [main]\n")
    assert probe_ci(other)["system"] == "azure-pipelines"


def test_probe_ci_none_when_absent(tmp_path: Path) -> None:
    ci = probe_ci(tmp_path)
    assert ci == {"system": "none", "paths": []}


def test_probe_deploy_resolves_helm_and_environments(tmp_path: Path) -> None:
    chart = tmp_path / "deploy" / "production" / "chart"
    (chart / "templates").mkdir(parents=True)
    (chart / "Chart.yaml").write_text("name: app\nversion: 0.1.0\n")
    (chart / "templates" / "d.yaml").write_text("apiVersion: apps/v1\nkind: Deployment\n")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")

    deploy = probe_deploy(tmp_path, probe_iac(tmp_path))
    assert deploy["system"] == "helm"  # helm beats k8s/docker by precedence
    assert "helm_charts" in deploy["manifests"]
    assert "dockerfiles" in deploy["manifests"]
    assert "production" in infer_environments(deploy)


def test_probe_deploy_argocd_wins(tmp_path: Path) -> None:
    app = tmp_path / "argo" / "staging-app.yaml"
    app.parent.mkdir(parents=True)
    app.write_text("apiVersion: argoproj.io/v1alpha1\nkind: Application\nmetadata:\n  name: a\n")
    deploy = probe_deploy(tmp_path, probe_iac(tmp_path))
    assert deploy["system"] == "argocd"
    assert "staging" in infer_environments(deploy)


def test_probe_deploy_dockerfile_only_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    deploy = probe_deploy(tmp_path, probe_iac(tmp_path))
    assert deploy["system"] == "unknown"


def test_probe_deploy_none_when_empty(tmp_path: Path) -> None:
    deploy = probe_deploy(tmp_path, probe_iac(tmp_path))
    assert deploy["system"] == "none"
    assert infer_environments(deploy) == []


def test_repomap_baseline_block_carries_delivery_surface() -> None:
    rm = RepoMap(
        available=True,
        repo="o/a",
        ci_system="github-actions",
        ci_pipeline_paths=[".github/workflows/ci.yml"],
        deploy_system="helm",
        deploy_environments=["dev"],
    )
    block = rm.to_baseline_block()
    assert block["ci_system"] == "github-actions"
    assert block["deploy_system"] == "helm"
    assert block["deploy_environments"] == ["dev"]


# ── #189: DORA client honesty ───────────────────────────────────────────────


def test_dora_unavailable_by_default_never_fabricates() -> None:
    d = dora_context("o/a", "production")
    assert d["available"] is False
    assert d["reason"]
    assert d["lead_time_p50_hours"] is None
    assert d["change_fail_rate"] is None
    # object-typed metrics are OMITTED (honest absence, schema-conforming)
    assert "deploy_success_rate" not in d
    assert "last_deploy" not in d


def test_dora_no_repo_is_unavailable() -> None:
    assert dora_context(None)["available"] is False


def test_dora_fixture_yields_available_record() -> None:
    d = dora_context(
        "o/a",
        "production",
        fixture={
            "deploy_success_rate": {"value": 0.94, "sample": 50},
            "lead_time_p50_hours": 6.5,
            "change_fail_rate": 0.08,
            "last_deploy": {"env": "production", "status": "success", "at": "2026-06-18T10:00:00Z"},
        },
    )
    assert d["available"] is True
    assert d["lead_time_p50_hours"] == 6.5
    assert d["deploy_success_rate"]["window_days"] == 30  # window folded in


def test_dora_partial_fixture_omits_missing_objects() -> None:
    d = dora_context("o/a", fixture={"lead_time_p50_hours": 3.0})
    assert d["available"] is True
    assert d["lead_time_p50_hours"] == 3.0
    assert d["change_fail_rate"] is None
    assert "deploy_success_rate" not in d  # not fabricated


def test_dora_failing_mcp_client_degrades_never_raises() -> None:
    class _Boom:
        def dora_metrics(self, repo: str, env=None, window_days=30) -> dict:  # noqa: ARG002
            raise RuntimeError("provider down")

    d = dora_context("o/a", mcp_client=_Boom())
    assert d["available"] is False
    assert "unreachable" in d["reason"]


def test_dora_mcp_unavailable_envelope_forces_null_metrics() -> None:
    class _Empty:
        def dora_metrics(self, repo: str, env=None, window_days=30) -> dict:  # noqa: ARG002
            # A provider returning available=False must not leak metrics.
            return {"available": False, "lead_time_p50_hours": 9.9, "reason": "no data"}

    d = dora_context("o/a", mcp_client=_Empty())
    assert d["available"] is False
    assert d["lead_time_p50_hours"] is None


# ── #190: the derived deployment block + policy ─────────────────────────────


def test_block_none_when_no_recon() -> None:
    assert assess_deployment_readiness(_plan(None), _epic()) is None


def test_block_none_for_library_no_deploy_surface() -> None:
    rm = RepoMap(available=True, repo="o/lib", ci_system="none", deploy_system="none")
    assert assess_deployment_readiness(_plan(rm), _epic()) is None


def test_production_helm_is_high_risk_and_gated() -> None:
    rm = RepoMap(
        available=True,
        repo="o/a",
        commit="abc",
        languages=["python"],
        ci_system="github-actions",
        ci_pipeline_paths=[".github/workflows/deploy.yml"],
        deploy_system="helm",
        deploy_manifests={"helm_charts": ["chart/Chart.yaml"]},
        deploy_environments=["staging", "production"],
    )
    block = assess_deployment_readiness(_plan(rm), _epic())
    assert block is not None
    _validate_block(block)
    assert block["risk_class"] == "high"
    assert block["production_classification"] == "production"
    assert "human-approval" in block["system_gates"]
    assert "iac-scan" in block["required_scans"]
    # production verification is plan-only and NEVER ephemeral/production apply
    assert block["deploy_verification"] == {"target_level": "VAL-2", "mode": "plan-only"}


def test_dev_only_no_ci_needs_pipeline() -> None:
    rm = RepoMap(
        available=True,
        repo="o/svc",
        commit="d",
        languages=["go"],
        ci_system="none",
        ci_pipeline_paths=[],
        deploy_system="kubectl",
        deploy_manifests={"k8s_manifests": ["dev/app.yaml"]},
        deploy_environments=["dev"],
    )
    block = assess_deployment_readiness(_plan(rm), _epic())
    assert block is not None
    _validate_block(block)
    assert block["needs_pipeline"] is True
    assert block["production_classification"] == "internal"
    assert block["risk_class"] == "medium"
    # missing DORA never relaxes a gate
    assert block["dora_context"]["available"] is False
    assert "ci-green" in block["system_gates"]


def test_missing_dora_keeps_readiness_unknown_not_healthy() -> None:
    rm = RepoMap(
        available=True,
        repo="o/a",
        deploy_system="helm",
        deploy_manifests={"helm_charts": ["c/Chart.yaml"]},
        deploy_environments=["staging"],
        ci_system="github-actions",
        ci_pipeline_paths=[".github/workflows/ci.yml"],
    )
    block = assess_deployment_readiness(_plan(rm), _epic())
    assert block is not None
    health = next(r for r in block["readiness"] if r["check"] == "delivery-health-known")
    assert health["status"] == "not_run"
    assert "UNKNOWN" in health["reason"]


def test_readiness_non_pass_always_has_reason() -> None:
    rm = RepoMap(
        available=True,
        repo="o/a",
        deploy_system="kubectl",
        deploy_manifests={"k8s_manifests": ["dev/app.yaml"]},
        deploy_environments=["dev"],
        ci_system="none",
    )
    block = assess_deployment_readiness(_plan(rm), _epic())
    assert block is not None
    for entry in block["readiness"]:
        if entry["status"] in ("fail", "not_run"):
            assert entry.get("reason"), entry


# ── AC injection + readiness check ──────────────────────────────────────────


def test_inject_deployment_acs_idempotent() -> None:
    rm = RepoMap(
        available=True,
        repo="o/a",
        deploy_system="helm",
        deploy_manifests={"helm_charts": ["c/Chart.yaml"]},
        deploy_environments=["staging"],
        ci_system="github-actions",
        ci_pipeline_paths=[".github/workflows/ci.yml"],
    )
    epic = _epic()
    block = assess_deployment_readiness(_plan(rm), epic)
    assert block is not None
    first = inject_deployment_acs(epic, block)
    assert first  # scans + rollback ACs added
    second = inject_deployment_acs(epic, block)
    assert second == []  # already covered → no duplicates


def test_needs_pipeline_adds_pipeline_ac() -> None:
    block = {"needs_pipeline": True, "required_scans": ["secret-scan", "sast"]}
    keys = [k for k, _ in deployment_acs(block)]
    assert keys[0] == "pipeline"


def test_deployment_pipeline_check_fails_when_needs_pipeline() -> None:
    epic = _epic()
    epic.deployment = {"needs_pipeline": True, "ci_system": "none", "deploy_system": "kubectl"}
    result = _REGISTRY["deployment-pipeline-present"](_plan(), epic, ReadinessContext())
    assert result.status == "fail"
    assert result.hard is True
    assert result.remediation


def test_deployment_pipeline_check_not_applicable_without_block() -> None:
    epic = _epic()
    result = _REGISTRY["deployment-pipeline-present"](_plan(), epic, ReadinessContext())
    assert result.status == "not_applicable"


def test_deployment_findings_flag_production() -> None:
    block = {
        "risk_class": "high",
        "production_classification": "production",
        "deploy_system": "helm",
        "required_scans": ["iac-scan"],
        "system_gates": ["ci-green", "human-approval"],
        "deploy_verification": {"target_level": "VAL-2", "mode": "plan-only"},
        "needs_pipeline": False,
    }
    titles = [f.title for f in deployment_findings(block)]
    assert any("human approval" in t.lower() for t in titles)
