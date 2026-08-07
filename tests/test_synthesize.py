"""Tests for the synthesize stage — Testing Strategy + CI/CD (issues #13/#14)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.decompose.models import ChildIssue, EpicPlan  # noqa: E402
from plan.models import Criterion, NormalizedPlan  # noqa: E402
from plan.recon.delta import compute_footprints  # noqa: E402
from plan.recon.models import RepoMap  # noqa: E402
from plan.synthesize.cicd_generator import generate_cicd  # noqa: E402
from plan.synthesize.run import synthesize  # noqa: E402
from plan.synthesize.testing_strategy import (  # noqa: E402
    generate_testing_strategy,
)


def _plan(title="Add API endpoint", desc="Add a REST API endpoint to the service.",
          kind="software", criteria=None):
    return NormalizedPlan(
        plan_id="001-add-api-endpoint",
        title=title,
        description=desc,
        source_format="markdown",
        target_kind=kind,
        criteria=[
            Criterion(id=f"AC#{i}", text=t)
            for i, t in enumerate(criteria or [], 1)
        ],
    )


def _software_plan():
    return _plan(criteria=[
        "The endpoint returns 200 for a valid request.",
        "The user can submit the form and see a confirmation.",
    ])


def _generic_plan():
    return _plan(
        title="Hiring campaign",
        desc="Plan a hiring campaign for the marketing team.",
        kind="non-software",
        criteria=["A shortlist of candidates is produced."],
    )


def test_software_plan_generates_both_artifacts():
    plan = _software_plan()

    cicd = generate_cicd(plan)
    testing = generate_testing_strategy(plan)

    assert cicd is not None
    assert testing is not None

    # Documents are non-empty markdown.
    assert cicd.document.strip()
    assert testing.document.strip()
    assert cicd.document.lstrip().startswith("#")
    assert testing.document.lstrip().startswith("#")

    # Kinds and filenames are correct.
    assert cicd.kind == "cicd"
    assert cicd.child.kind == "cicd"
    assert cicd.filename == "docs/plans/001-add-api-endpoint-cicd-pipeline.md"

    assert testing.kind == "testing"
    assert testing.child.kind == "testing"
    assert testing.filename == "docs/plans/001-add-api-endpoint-testing-strategy.md"

    # Child keys + key labels.
    assert cicd.child.key == "CICD"
    assert "area:cicd" in cicd.child.labels
    assert testing.child.key == "TEST"
    assert "handover:tfactory" in testing.child.labels


def test_testing_doc_maps_each_acceptance_criterion():
    plan = _software_plan()
    testing = generate_testing_strategy(plan)
    assert testing is not None

    # Every AC id appears in the AC → approach mapping table.
    for c in plan.criteria:
        assert c.id in testing.document
    assert "Test approach" in testing.document


def test_cicd_includes_container_and_terraform_stages_on_signals():
    plan = _plan(
        desc="Provision a Kubernetes cluster with Terraform and deploy via Helm.",
        criteria=["The service is reachable in the cluster."],
    )
    cicd = generate_cicd(plan)
    assert cicd is not None
    doc = cicd.document.lower()
    assert "containerise" in doc
    assert "cluster" in doc
    assert "terraform" in doc


def test_non_software_plan_synthesizes_nothing():
    plan = _generic_plan()
    assert generate_cicd(plan) is None
    assert generate_testing_strategy(plan) is None


def test_synthesize_appends_two_children_with_sane_graph():
    plan = _software_plan()
    epic = EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        children=[ChildIssue(key="C1", title="Implement endpoint")],
    )

    artifacts = synthesize(plan, epic)

    assert len(artifacts) == 2
    assert len(epic.children) == 3  # C1 + TEST + CICD
    keys = {c.key for c in epic.children}
    assert {"C1", "TEST", "CICD"} <= keys
    assert epic.validate_dependencies() == []


def test_synthesize_is_idempotent_on_keys():
    plan = _software_plan()
    epic = EpicPlan(plan_id=plan.plan_id, epic_title=plan.title)

    synthesize(plan, epic)
    synthesize(plan, epic)

    # Running twice does not duplicate the synthesized children.
    assert len(epic.children) == 2
    assert epic.validate_dependencies() == []


# ── AIFactory#1113: the CI/CD child must target the pipeline, not a doc ──────


def _cicd_footprint(plan, repo_map):
    """The contract footprint the delta pass derives for the CI/CD child.

    Goes through the real machinery (synthesize -> compute_footprints) because
    that chain IS the defect: the child's text is the only source of a file
    target, so whatever the body names is what the coder is told to touch.
    """
    plan.repo_map = repo_map
    epic = EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        children=[ChildIssue(key="C1", title="Implement endpoint")],
    )
    synthesize(plan, epic)
    return compute_footprints(plan, epic).get("CICD", {})


def test_cicd_child_names_the_pipeline_file_not_a_design_doc():
    # Greenfield: no RepoMap at all, so the default pipeline path is used.
    cicd = generate_cicd(_software_plan())
    assert cicd is not None

    assert ".github/workflows/ci.yml" in cicd.child.body
    # The dangling docs/plans/... reference is the path the coder used to create.
    assert "docs/plans/" not in cicd.child.body


def test_cicd_footprint_modifies_the_discovered_pipeline():
    repo_map = RepoMap(
        available=True,
        ci_system="github-actions",
        ci_pipeline_paths=[".github/workflows/ci.yml"],
        layout={"files": ["pyproject.toml"], "dirs": ["src"]},
    )
    fp = _cicd_footprint(_software_plan(), repo_map)

    assert ".github/workflows/ci.yml" in fp["files_to_modify"]
    # Regression: the only file it used to create was the design document.
    assert not [f for f in fp["files_to_create"] if f.endswith(".md")]


def test_cicd_footprint_creates_the_default_pipeline_when_repo_has_none():
    repo_map = RepoMap(available=True, ci_system="gitlab-ci", layout={"files": ["go.mod"]})
    fp = _cicd_footprint(_software_plan(), repo_map)

    assert ".gitlab-ci.yml" in fp["files_to_create"] + fp["files_to_modify"]


# ── PFactory#461: the testing child must target test files, not a doc ────────


def _testing(plan, repo_map):
    """The testing child and the footprint the delta pass derives for it.

    Same machinery as ``_cicd_footprint`` and for the same reason: the child's
    text is the only source of a file target, so the footprint — not the prose —
    is what the coder is handed.
    """
    plan.repo_map = repo_map
    epic = EpicPlan(
        plan_id=plan.plan_id,
        epic_title=plan.title,
        children=[ChildIssue(key="C1", title="Implement endpoint")],
    )
    synthesize(plan, epic)
    child = next(c for c in epic.children if c.key == "TEST")
    return child, compute_footprints(plan, epic).get("TEST", {})


def test_testing_child_names_test_files_not_a_design_doc():
    # Greenfield: no RepoMap, so the language comes from the spec text.
    plan = _plan(desc="Add a REST API endpoint to the Python service, tested with pytest.")
    testing = generate_testing_strategy(plan)
    assert testing is not None

    # A directory alone would mine nothing: _FILE_TOKEN needs an extension.
    assert "tests/test_add_api_endpoint_unit.py" in testing.child.body
    # The dangling docs/plans/... reference is the path the coder used to create.
    assert "docs/plans/" not in testing.child.body


def test_testing_footprint_creates_real_test_files():
    repo_map = RepoMap(
        available=True,
        languages=["python"],
        layout={"files": ["pyproject.toml"], "dirs": ["src"]},
    )
    _child, fp = _testing(_software_plan(), repo_map)

    assert fp["files_to_create"] == [
        "tests/test_add_api_endpoint_e2e.py",
        "tests/test_add_api_endpoint_integration.py",
        "tests/test_add_api_endpoint_unit.py",
    ]
    # Regression: the only file it used to create was the design document.
    assert not [f for f in fp["files_to_create"] if f.endswith(".md")]


def test_testing_child_uses_the_repos_own_test_dir_and_command():
    # An already-tested repo: new tests land in the tree it already has (test/,
    # not tests/) and run under the command reconnaissance already found.
    repo_map = RepoMap(
        available=True,
        languages=["typescript"],
        layout={"files": ["package.json"], "dirs": ["src", "test"]},
        existing_test_command="npm run test",
    )
    child, fp = _testing(_software_plan(), repo_map)

    assert fp["files_to_create"] == [
        "test/add_api_endpoint_e2e.test.ts",
        "test/add_api_endpoint_integration.test.ts",
        "test/add_api_endpoint_unit.test.ts",
    ]
    assert "`npm run test`" in child.body


def test_testing_child_names_no_files_for_an_unmapped_language():
    # C# has no entry in the test-layout table, and its extension is not one the
    # footprint miner recognises anyway, so the child names no path at all rather
    # than a plausible Python one (#585).
    repo_map = RepoMap(available=True, languages=["csharp"], layout={"dirs": ["src"]})
    child, fp = _testing(_software_plan(), repo_map)

    assert fp == {}
    assert "docs/plans/" not in child.body
