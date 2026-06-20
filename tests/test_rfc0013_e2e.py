"""RFC-0013 end-to-end proof: discover -> derive -> emit -> verify (Factory#152).

This is the *end-to-end* counterpart to ``test_deployment_aware_planning.py``
(which unit-tests the three new modules in isolation). Here we drive the WHOLE
producing chain from a real checkout on disk, exactly as the planner does it in
``plan.recon.reconnoiter.build_repo_map``:

    files on disk
      -> probe_ci / probe_deploy / probe_iac (static recon, #188)
      -> RepoMap (the planner's view of the delivery surface, #188)
      -> assess_deployment_readiness (the derived ``deployment`` block, #190)
      -> jsonschema.validate against the vendored ``$defs.deployment`` (#190)
      -> deployment_acs / inject_deployment_acs (the implicit deploy ACs)

It proves the contract the rest of the fleet consumes:

  (a) DEPLOYMENT-BEARING case — a service WITH CI + Helm/Terraform deploy
      manifests yields a populated, schema-valid ``deployment`` block: correct
      ``ci_system``/``ci_exists``, detected ``deploy_system``, a ``risk_class``
      (high for production), the ``required_scans`` + ``system_gates`` floors, a
      ``readiness`` checklist where every non-pass carries a reason, and a
      ``dora_context`` that honestly degrades to ``available: false`` (no MCP).

  (b) NO-PIPELINE case — a deployable repo with NO CI sets ``needs_pipeline``,
      fails the ci-pipeline-present readiness check (with a reason), and injects a
      pipeline deployment AC for AIFactory to scaffold.

  (c) DEPLOY VERIFICATION POLICY — deploy verification is DRY-RUN by policy and
      NEVER claims VAL-4: production caps at a plan/dry-run, and the planned
      ladder of the Factory ``deploy`` profile tops out at VAL-3 (no autonomous
      production apply). The Factory ``scripts/verification_profiles.py`` is used
      to assert the profile semantics when importable; the block-level policy is
      asserted directly in every run.

Building the RepoMap from a real tree (not a hand-set fixture) is the point: it
exercises the discovery code the same way production does, so this test fails if
recon, derivation, the schema, or the policy drift apart.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = (Path(__file__).parent.parent / "apps" / "backend").resolve()
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
jsonschema = pytest.importorskip("jsonschema")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.feasibility.deployment import (  # noqa: E402
    assess_deployment_readiness,
    deployment_acs,
    inject_deployment_acs,
)
from plan.models import NormalizedPlan  # noqa: E402
from plan.recon.models import RepoMap  # noqa: E402
from plan.recon.reconnoiter import build_repo_map  # noqa: E402

_SCHEMA_PATH = _BACKEND / "plan" / "emit" / "contracts" / "task-contract.schema.json"

# Best-effort import of the Factory hub deploy verification profile (the source of
# the deploy-policy semantics). Vendored side-by-side in dev; absent in CI where
# only PFactory is checked out — the block-level policy assertions then stand
# alone (per Factory#152's "else assert the block's deploy_verification fields").
_FACTORY_PROFILES = (
    _BACKEND.parent.parent.parent / "Factory" / "scripts" / "verification_profiles.py"
)


def _load_factory_profiles() -> Any | None:
    if not _FACTORY_PROFILES.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "_factory_verification_profiles", _FACTORY_PROFILES
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - a broken sibling never fails the e2e proof
        return None
    return module


def _validate_block(block: dict[str, Any]) -> None:
    """Validate a deployment block against the authoritative vendored $defs.deployment."""
    schema = json.loads(_SCHEMA_PATH.read_text())
    dep_schema = {"$defs": schema["$defs"], **schema["$defs"]["deployment"]}
    jsonschema.validate(block, dep_schema)


def _repo_map(root: Path, repo: str) -> RepoMap:
    """Reconnoitre a real checkout exactly as the planner does (build_repo_map)."""
    return build_repo_map(root, repo=repo, base_ref="main", commit="deadbeef")


def _plan(repo_map: RepoMap) -> NormalizedPlan:
    return NormalizedPlan(
        plan_id="p-e2e",
        title="Add a metrics endpoint and ship it",
        source_format="markdown",
        repo_map=repo_map,
        change_mode="modify",
    )


def _epic() -> EpicPlan:
    return EpicPlan(
        plan_id="p-e2e",
        epic_title="Add metrics endpoint",
        children=[
            ChildIssue(
                key="c1",
                title="Implement /metrics",
                kind="feature",
                acceptance_criteria=["GET /metrics returns 200 with Prometheus text"],
            )
        ],
    )


def _write(root: Path, rel: str, text: str) -> None:
    fp = root / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(text)


def _make_deploying_service(root: Path, *, with_ci: bool, prod: bool) -> None:
    """A realistic service checkout: code + CI + Helm + Terraform deploy manifests.

    ``with_ci`` toggles a GitHub Actions deploy workflow; ``prod`` toggles a
    production-named manifest path so environment inference classifies it
    production (drives the high-risk policy).
    """
    # A bit of application code so the stack detector sees a real language.
    _write(root, "pyproject.toml", '[project]\nname = "svc"\nversion = "0.1.0"\n')
    _write(root, "app/__init__.py", "")
    _write(root, "app/main.py", "def metrics():\n    return 'ok'\n")
    _write(root, "Dockerfile", "FROM python:3.12-slim\nCOPY . /app\n")

    if with_ci:
        _write(
            root,
            ".github/workflows/deploy.yml",
            "name: deploy\non:\n  push:\n    branches: [main]\njobs:\n"
            "  deploy:\n    runs-on: ubuntu-latest\n    steps:\n      - run: helm upgrade\n",
        )

    # Helm chart under an environment-named path so infer_environments sees the env.
    env_dir = "production" if prod else "dev"
    chart = f"deploy/{env_dir}/chart"
    _write(root, f"{chart}/Chart.yaml", "name: svc\nversion: 0.1.0\n")
    _write(
        root,
        f"{chart}/templates/deployment.yaml",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: svc\n",
    )

    # Terraform so the IaC inventory + blast-radius surface a real deploy surface.
    _write(
        root,
        f"infra/{env_dir}/main.tf",
        'resource "aws_ecr_repository" "svc" {\n  name = "svc"\n}\n',
    )


# ── (a) DEPLOYMENT-BEARING case ─────────────────────────────────────────────


def test_e2e_deployment_bearing_block_is_populated_and_schema_valid(tmp_path: Path) -> None:
    """A service WITH CI + Helm/Terraform manifests => a full, schema-valid block."""
    _make_deploying_service(tmp_path, with_ci=True, prod=True)
    rm = _repo_map(tmp_path, "octo/svc")

    # Recon really saw the delivery surface (not a hand-set fixture).
    assert rm.available is True
    assert rm.ci_system == "github-actions"
    assert rm.ci_pipeline_paths == [".github/workflows/deploy.yml"]
    assert rm.deploy_system == "helm"  # helm wins over k8s/terraform/docker by precedence
    assert "production" in rm.deploy_environments

    block = assess_deployment_readiness(_plan(rm), _epic())
    assert block is not None
    _validate_block(block)  # producer cannot drift from $defs.deployment

    # CI fields are correct + a pipeline already exists (rides it, no scaffolding).
    assert block["ci_system"] == "github-actions"
    assert block["ci_exists"] is True
    assert block["needs_pipeline"] is False
    assert block["deploy_system"] == "helm"

    # Production => high risk, the full scan floor, and the human-approval gate.
    assert block["production_classification"] == "production"
    assert block["risk_class"] == "high"
    for scan in ("secret-scan", "sast", "dependency-audit", "iac-scan"):
        assert scan in block["required_scans"]
    assert "ci-green" in block["system_gates"]
    assert "human-approval" in block["system_gates"]

    # readiness[] is populated and every non-pass carries a reason (RFC-0013 §5).
    assert block["readiness"]
    for entry in block["readiness"]:
        assert entry["status"] in ("pass", "fail", "not_run")
        if entry["status"] != "pass":
            assert entry.get("reason"), entry

    # DORA degrades honestly with no MCP wired in: UNKNOWN, never fabricated.
    dora = block["dora_context"]
    assert dora["available"] is False
    assert dora["reason"]
    assert dora["lead_time_p50_hours"] is None
    assert dora["change_fail_rate"] is None
    health = next(r for r in block["readiness"] if r["check"] == "delivery-health-known")
    assert health["status"] == "not_run"
    assert "UNKNOWN" in health["reason"]


# ── (b) NO-PIPELINE case ────────────────────────────────────────────────────


def test_e2e_no_pipeline_sets_needs_pipeline_fails_readiness_and_injects_ac(tmp_path: Path) -> None:
    """A deployable repo with NO CI => needs_pipeline + readiness fail + injected AC."""
    _make_deploying_service(tmp_path, with_ci=False, prod=False)
    rm = _repo_map(tmp_path, "octo/svc-nopipe")

    assert rm.ci_system == "none"
    assert rm.ci_pipeline_paths == []
    assert rm.deploy_system == "helm"  # deployable surface exists...

    epic = _epic()
    block = assess_deployment_readiness(_plan(rm), epic)
    assert block is not None
    _validate_block(block)

    # ...but no usable CI to ride => the task must create/extend a pipeline.
    assert block["needs_pipeline"] is True
    assert block["ci_exists"] is False

    # The ci-pipeline-present readiness check fails WITH a reason (RFC-0013 §5).
    ci_check = next(r for r in block["readiness"] if r["check"] == "ci-pipeline-present")
    assert ci_check["status"] == "fail"
    assert ci_check.get("reason")

    # A pipeline deployment AC is offered, ordered first, for AIFactory to scaffold.
    ac_keys = [k for k, _ in deployment_acs(block)]
    assert ac_keys[0] == "pipeline"

    # And it is actually injected onto the epic's feature child (idempotently).
    before = list(epic.children[0].acceptance_criteria)
    injected = inject_deployment_acs(epic, block)
    assert any("pipeline" in text.lower() for text in injected)
    after = epic.children[0].acceptance_criteria
    assert len(after) == len(before) + len(injected)
    # Re-running injects nothing new (coverage is keyword-idempotent).
    assert inject_deployment_acs(epic, block) == []


# ── (c) DEPLOY VERIFICATION POLICY: dry-run, never VAL-4 ─────────────────────


def test_e2e_deploy_verification_is_dry_run_and_never_claims_val4(tmp_path: Path) -> None:
    """Deploy verification is DRY-RUN by policy and never claims an autonomous VAL-4."""
    _make_deploying_service(tmp_path, with_ci=True, prod=True)
    rm = _repo_map(tmp_path, "octo/svc")
    block = assess_deployment_readiness(_plan(rm), _epic())
    assert block is not None

    verification = block["deploy_verification"]
    # Production verification stops at a plan/dry-run; the real apply is held behind
    # the human-approval system gate (RFC-0013 §6).
    assert verification["mode"] in ("dry-run", "plan-only", "ephemeral-apply")
    assert verification["mode"] == "plan-only"  # production forces plan-only
    # NEVER an autonomous production apply / VAL-4.
    assert verification["mode"] != "production-apply"
    assert verification["target_level"] != "VAL-4"
    assert "human-approval" in block["system_gates"]

    # Cross-check the Factory deploy verification PROFILE semantics when importable:
    # the planned ladder for a deploy artifact caps at VAL-3 (no VAL-4 rung).
    profiles = _load_factory_profiles()
    if profiles is not None:
        deploy_files = [".github/workflows/deploy.yml", "deploy/production/chart/Chart.yaml"]
        assert profiles.detect_artifact_type(deploy_files) == "deploy"
        plan = profiles.plan_verification(deploy_files, available={"ci_linter"})
        assert plan["target_level"] == "VAL-3"  # tops out at VAL-3 by design
        levels = {lvl["level"] for lvl in plan["levels"]}
        assert "VAL-4" not in levels  # production apply is never in the autonomous ladder
        assert profiles.credential_class_for("deploy") == "C-ephemeral-target"


def test_e2e_non_production_caps_below_production_apply(tmp_path: Path) -> None:
    """A non-production deployable change is still dry-run/ephemeral, never prod apply."""
    _make_deploying_service(tmp_path, with_ci=True, prod=False)
    rm = _repo_map(tmp_path, "octo/svc-dev")
    block = assess_deployment_readiness(_plan(rm), _epic())
    assert block is not None
    _validate_block(block)

    assert block["production_classification"] != "production"
    verification = block["deploy_verification"]
    assert verification["mode"] != "production-apply"
    assert verification["target_level"] != "VAL-4"
