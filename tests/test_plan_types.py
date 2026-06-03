"""Tests for plan-type descriptors (issue #7)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from plan.models import NormalizedPlan  # noqa: E402
from plan.plan_types import apply, load_descriptors, select_for  # noqa: E402


def _plan(title="X", desc="", kind="software", criteria=None):
    from plan.models import Criterion

    return NormalizedPlan(
        plan_id="001-x",
        title=title,
        description=desc,
        source_format="markdown",
        target_kind=kind,
        criteria=[Criterion(id=f"AC#{i}", text=t) for i, t in enumerate(criteria or [], 1)],
    )


def test_all_descriptors_load():
    d = load_descriptors()
    assert {"software-service", "data-pipeline", "infra-change", "generic-deliverable"} <= set(d)


def test_software_service_default_for_software():
    plan = _plan(desc="Add a feature to the app.", kind="software")
    assert select_for(plan).name == "software-service"


def test_data_pipeline_selected_by_keywords():
    plan = _plan(desc="Build an ETL pipeline with Kafka ingestion into the warehouse.",
                 kind="software")
    assert select_for(plan).name == "data-pipeline"


def test_infra_change_selected_by_keywords():
    plan = _plan(desc="Provision a Kubernetes cluster with Terraform and an ingress.",
                 kind="software")
    d = select_for(plan)
    assert d.name == "infra-change"
    assert d.stages.synthesize_testing is False
    assert d.stages.code_gates is True


def test_non_software_gets_generic_no_code_stages():
    plan = _plan(desc="Plan a hiring campaign.", kind="non-software")
    d = select_for(plan)
    assert d.name == "generic-deliverable"
    assert d.stages.synthesize_cicd is False
    assert d.stages.code_gates is False
    assert d.stages.decompose is True


def test_apply_sets_plan_type_and_rehashes():
    plan = _plan(desc="Add a REST API endpoint.", kind="software").with_hash()
    out = apply(plan)
    assert out.plan_type == "software-service"
    assert out.hash_matches()
