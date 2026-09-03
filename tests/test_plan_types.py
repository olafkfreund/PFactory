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


def test_platform_does_not_score_as_form():
    # PFactory#673: substring matching let feature's `form` keyword score
    # inside "platform" ("cross-platform framework", "on each platform") — and
    # a fabricated point can decide a scoring tie, which gates pipeline stages.
    # With word-boundary matching this spec has ZERO keyword hits, so it must
    # fall back to software-service; under the bug, feature scored 1 and won.
    plan = _plan(
        desc="Ship it natively on each platform, with no cross-platform framework.",
        kind="software",
    )
    assert select_for(plan).name == "software-service"


def test_markets_plural_still_scores_for_product_planning():
    # The audit half of PFactory#673: "market" only ever matched "Markets at
    # launch" via the substring bug. The plural is now an explicit keyword.
    plan = _plan(desc="Markets at launch: the UK, the EU, and the US.",
                 kind="non-software")
    assert select_for(plan).name == "product-planning"


def test_ui_does_not_score_inside_ordinary_words():
    # "ui" as a substring matched "requires", "quite", "guide", "suitable" —
    # phantom software-service points on prose with no UI at all.
    plan = _plan(desc="This requires quite a suitable guide for building it.",
                 kind="software")
    d = select_for(plan)
    assert d.name == "software-service"  # via fallback (score 0), not a ui hit
    # Prove it is the fallback, not a keyword win: an actual keyword elsewhere
    # must beat it outright.
    kafka = _plan(desc="This requires quite a suitable guide to Kafka ingestion.",
                  kind="software")
    assert select_for(kafka).name == "data-pipeline"


def test_apply_sets_plan_type_and_rehashes():
    plan = _plan(desc="Add a REST API endpoint.", kind="software").with_hash()
    out = apply(plan)
    assert out.plan_type == "software-service"
    assert out.hash_matches()
