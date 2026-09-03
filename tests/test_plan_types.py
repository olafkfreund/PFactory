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
    # Neutral software text (no descriptor keywords) falls back to software-service.
    plan = _plan(desc="Improve the application for our users.", kind="software")
    assert select_for(plan).name == "software-service"


def test_feature_plan_type_selected_by_keyword():
    plan = _plan(desc="Add a feature: a user story for the checkout flow.", kind="software")
    assert select_for(plan).name == "feature"


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


def test_mobile_app_selected_for_mobile_spec():
    # Both directions matter: a mobile spec must WIN over software-service,
    # and a plain backend spec must still select software-service.
    mobile = _plan(
        title="MyFriends mobile app",
        desc="A native iOS and Android app (Swift / Kotlin) for finding nearby "
        "people open to new friends, distributed via the App Store and Play Store.",
        kind="software",
    )
    d = select_for(mobile)
    assert d.name == "mobile-app"
    assert d.category == "mobile"
    # all five stages on — mobile apps get the full software deep path
    assert d.stages.synthesize_testing and d.stages.synthesize_cicd
    assert d.stages.code_gates and d.stages.decompose and d.stages.review

    backend = _plan(
        desc="A REST API backend service with endpoints and a webhook.",
        kind="software",
    )
    assert select_for(backend).name == "software-service"


def test_apply_sets_plan_type_and_rehashes():
    plan = _plan(desc="Add a REST API endpoint.", kind="software").with_hash()
    out = apply(plan)
    assert out.plan_type == "software-service"
    assert out.hash_matches()
