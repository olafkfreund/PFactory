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
