"""Tests for RFC-0010 Phase 4: delta-aware emit, approval hash, schema sync."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.emit import task_contract as tc  # noqa: E402
from plan.emit.contract_builder import build_task_contract  # noqa: E402
from plan.emit.contract_emit import assemble_contract  # noqa: E402
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.recon import RepoMap  # noqa: E402
from plan.recon.delta import compute_footprints  # noqa: E402

_EKS_REPO_MAP = RepoMap(
    available=True,
    repo="o/infra",
    base_ref="main",
    commit="deadbeef12345678",
    languages=["hcl"],
    iac=["terraform"],
    iac_resources={
        "terraform": {
            "resources": [
                {"type": "aws_eks_cluster", "name": "main", "file": "eks.tf"},
                {
                    "type": "aws_eks_node_group",
                    "name": "workers",
                    "file": "node_groups.tf",
                },
            ],
            "files": ["eks.tf", "node_groups.tf"],
            "modules": [],
            "providers": ["aws"],
        }
    },
)


def _plan(
    repo_map=None, change_mode=None, crits=("Increase the EKS node group max size",)
):
    return NormalizedPlan(
        plan_id="001-eks",
        title="Scale EKS",
        description="Increase node group capacity",
        source_format="markdown",
        criteria=[Criterion(id=f"AC#{i}", text=t) for i, t in enumerate(crits, 1)],
        repo_map=repo_map,
        change_mode=change_mode,
    )


def _epic():
    return EpicPlan(
        plan_id="001-eks",
        epic_title="Scale EKS",
        children=[
            ChildIssue(
                key="C1",
                title="Raise the EKS node group max size",
                body="bump max in the node group",
                kind="infra",
                acceptance_criteria=["Increase the EKS node group max size"],
            )
        ],
    )


# ── delta footprints (Scenario A) ───────────────────────────────────────


def test_compute_footprints_maps_eks_node_group():
    fp = compute_footprints(_plan(_EKS_REPO_MAP), _epic())
    assert "node_groups.tf" in fp["C1"]["files_to_modify"]


def test_compute_footprints_empty_when_greenfield():
    assert compute_footprints(_plan(None), _epic()) == {}


# ── delta-aware contract ────────────────────────────────────────────────


def test_contract_carries_baseline_change_mode_footprints():
    plan = _plan(_EKS_REPO_MAP, change_mode="modify")
    c = build_task_contract(plan, _epic(), repo="o/infra")
    assert c["change_mode"] == "modify"
    assert c["baseline"]["repo"] == "o/infra"
    assert c["provenance"]["baseline_commit"] == "deadbeef12345678"
    assert c["provenance"]["base_ref"] == "main"
    sub = c["phases"][0]["subtasks"][0]
    assert "node_groups.tf" in sub["files_to_modify"]


def test_greenfield_contract_has_no_baseline():
    c = build_task_contract(_plan(None), _epic(), repo="o/infra")
    assert "baseline" not in c and "change_mode" not in c
    assert c["phases"][0]["subtasks"][0]["files_to_modify"] == []


def test_full_contract_language_from_repo_and_validates():
    # A python repo: emitted environment.language must come from the repo, and the
    # ac_to_code_map must be pre-seeded; the whole contract must validate.
    rm = RepoMap(
        available=True,
        repo="o/api",
        commit="c0ffee00",
        languages=["python"],
        layout={"files": ["app.py"], "dirs": ["tests"]},
    )
    plan = _plan(rm, change_mode="modify", crits=("app.py returns 200 on /health",))
    c = assemble_contract(plan, _epic(), repo="o/api")
    assert c["environment"]["language"] == "python"
    assert "AC#1" in c["tfactory"]["ac_to_code_map"]
    assert tc.validate_contract(c) == []  # schema-valid


# ── approval-hash digest (idempotent on same commit) ────────────────────


def test_hash_stable_on_same_commit_changes_on_drift():
    base = _plan(_EKS_REPO_MAP, change_mode="modify").with_hash()
    same = base.model_copy(update={"repo_map": _EKS_REPO_MAP.model_copy()})
    assert same.compute_hash() == base.content_hash  # idempotent
    drifted = base.model_copy(
        update={"repo_map": _EKS_REPO_MAP.model_copy(update={"commit": "newsha999"})}
    )
    assert drifted.compute_hash() != base.content_hash  # drift invalidates


def test_greenfield_hash_unchanged_by_recon_fields():
    # A plan with no repo_map hashes exactly as before RFC-0010.
    p = _plan(None)
    expected_parts = [
        p.title,
        p.description,
        p.target_kind,
        "",
        "AC#1:" + p.criteria[0].text,
    ]
    assert p.canonical_content() == "\n".join(expected_parts)


# ── schema sync / drift guard ───────────────────────────────────────────


def test_vendored_schema_has_rfc0010_fields():
    s = json.loads(tc._SCHEMA_PATH.read_text())
    assert "change_mode" in s["properties"]
    assert "baseline" in s["$defs"]
    assert "source_language" in s["$defs"]["environment"]["properties"]
    assert "equivalence" in s["properties"]["tfactory"]["properties"]
    assert (
        "equivalence"
        in s["properties"]["tfactory"]["properties"]["lanes"]["items"]["enum"]
    )


def test_fallback_enums_match_vendored_schema():
    s = json.loads(tc._SCHEMA_PATH.read_text())
    assert tc._CHANGE_MODES == set(s["properties"]["change_mode"]["enum"])
    assert tc._TFACTORY_LANES == set(
        s["properties"]["tfactory"]["properties"]["lanes"]["items"]["enum"]
    )
    assert tc._WORKFLOW_TYPES == set(s["properties"]["workflow_type"]["enum"])
