"""Tests for the software/non-software target classifier (issue #5)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.detect.target_classifier import (  # noqa: E402
    apply,
    classify_plan,
    classify_text,
)
from plan.models import NormalizedPlan  # noqa: E402


def test_classifies_clear_software():
    text = (
        "Add a REST API endpoint to the payments microservice. Update the "
        "database schema, write a migration, add unit tests, and deploy via "
        "the Kubernetes Helm chart."
    )
    r = classify_text(text)
    assert r.kind == "software"
    assert r.software_score > r.non_software_score
    assert r.confidence > 0.6


def test_classifies_clear_non_software():
    text = (
        "Plan a Q3 marketing campaign and hiring drive: draft a whitepaper, "
        "set the budget, and prepare the brand presentation for stakeholders."
    )
    r = classify_text(text)
    assert r.kind == "non-software"
    assert r.non_software_score > r.software_score


def test_empty_or_neutral_is_undetermined():
    assert classify_text("").kind == "undetermined"
    assert classify_text("Make things better for everyone.").kind == "undetermined"


def test_classify_plan_reads_all_fields():
    plan = NormalizedPlan(
        plan_id="001-x",
        title="New service",
        description="Build a backend API with a database and CI/CD pipeline.",
        source_format="markdown",
    )
    assert classify_plan(plan).kind == "software"


def test_apply_sets_target_kind_and_rehashes():
    plan = NormalizedPlan(
        plan_id="001-x",
        title="Refactor the authentication service",
        description="Replace JWT handling in the backend API.",
        source_format="markdown",
    ).with_hash()
    out = apply(plan)
    assert out.target_kind == "software"
    assert out.hash_matches()  # re-hashed so approval contract stays valid
