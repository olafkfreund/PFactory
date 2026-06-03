"""Tests for the NormalizedPlan data contract (issue #3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.models import (  # noqa: E402
    Criterion,
    NormalizedPlan,
    make_plan_id,
    slugify,
)
from spec_sources import (  # noqa: E402
    AcceptanceCriterion,
    NormalizedSpec,
    SpecFormat,
)


def _spec() -> NormalizedSpec:
    return NormalizedSpec(
        title="Add Refund Endpoint",
        description="Let users request refunds.",
        criteria=(
            AcceptanceCriterion(id="AC#1", text="User can request a refund"),
            AcceptanceCriterion(id="AC#2", text="Refunds are audited"),
        ),
        source_format=SpecFormat.MARKDOWN,
    )


# ── slug / id ──────────────────────────────────────────────────────────

def test_slugify_basic():
    assert slugify("Add Refund Endpoint!") == "add-refund-endpoint"
    assert slugify("  Spaces  &  Symbols  ") == "spaces-symbols"
    assert slugify("") == "plan"


def test_make_plan_id_format():
    assert make_plan_id(1, "Add Refund Endpoint") == "001-add-refund-endpoint"
    assert make_plan_id(42, "Search") == "042-search"


# ── from_spec ──────────────────────────────────────────────────────────

def test_from_spec_maps_fields_and_hashes():
    plan = NormalizedPlan.from_spec(_spec(), seq=3, source_channel="portal")
    assert plan.plan_id == "003-add-refund-endpoint"
    assert plan.title == "Add Refund Endpoint"
    assert plan.source_format == "markdown"
    assert plan.source_channel == "portal"
    assert [c.id for c in plan.criteria] == ["AC#1", "AC#2"]
    assert plan.target_kind == "undetermined"
    assert plan.content_hash and plan.hash_matches()


def test_explicit_plan_id_overrides_seq():
    plan = NormalizedPlan.from_spec(_spec(), plan_id="010-custom")
    assert plan.plan_id == "010-custom"


# ── hashing / approval invalidation ────────────────────────────────────

def test_hash_is_stable_across_enrichment_changes():
    plan = NormalizedPlan.from_spec(_spec())
    h0 = plan.content_hash
    # Re-running enrichment / restamping must NOT invalidate the hash.
    enriched = plan.model_copy(
        update={
            "enrichment": {"infra": [{"adapter": "kubernetes"}], "knowledge": []},
            "ingested_at": "2099-01-01T00:00:00+00:00",
        }
    )
    assert enriched.compute_hash() == h0
    assert enriched.hash_matches()


def test_editing_substance_invalidates_hash():
    plan = NormalizedPlan.from_spec(_spec())
    edited = plan.model_copy(
        update={"criteria": [*plan.criteria, Criterion(id="AC#3", text="New rule")]}
    )
    assert not edited.hash_matches()  # stored hash no longer matches content
    assert edited.with_hash().hash_matches()  # re-hash fixes it


# ── serialization ──────────────────────────────────────────────────────

def test_json_round_trip():
    plan = NormalizedPlan.from_spec(_spec())
    restored = NormalizedPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.hash_matches()
