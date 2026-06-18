"""Tests for the RFC-0010 RepoMap data contract + plan plumbing (Phase 1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.models import NormalizedPlan  # noqa: E402
from plan.recon import RepoMap  # noqa: E402


def _plan() -> NormalizedPlan:
    return NormalizedPlan(plan_id="001-x", title="T", source_format="markdown")


# ── defaults / back-compat ──────────────────────────────────────────────

def test_repo_map_defaults_to_unavailable():
    rm = RepoMap()
    assert rm.available is False
    assert rm.repo is None and rm.languages == [] and rm.iac_resources == {}


def test_plan_repo_map_defaults_none():
    assert _plan().repo_map is None


# ── to_baseline_block emits only populated fields ───────────────────────

def test_baseline_block_compact_when_unavailable():
    assert RepoMap(available=False, error="private").to_baseline_block() == {
        "available": False,
        "error": "private",
    }


def test_baseline_block_includes_populated_fields():
    rm = RepoMap(
        available=True,
        repo="o/r",
        commit="abc123",
        languages=["python"],
        iac=["terraform"],
        iac_resources={"terraform": {"resources": [{"type": "aws_eks_cluster"}]}},
    )
    block = rm.to_baseline_block()
    assert block["available"] is True
    assert block["repo"] == "o/r" and block["commit"] == "abc123"
    assert block["languages"] == ["python"] and block["iac"] == ["terraform"]
    assert "error" not in block  # empty fields are dropped
    assert "frameworks" not in block


# ── approval hash: unavailable recon is inert; available recon is digested ─

def test_unavailable_repo_map_does_not_change_canonical_content():
    plan = _plan()
    greenfield = plan.model_copy(update={"repo_map": RepoMap(available=False)})
    assert greenfield.canonical_content() == plan.canonical_content()


def test_available_repo_map_folds_into_canonical_content():
    # RFC-0010 Phase 4: a grounded plan's hash includes a stable recon digest so
    # approval invalidates when the repo drifts.
    plan = _plan()
    grounded = plan.model_copy(
        update={"repo_map": RepoMap(available=True, repo="o/r", commit="abc"), "change_mode": "modify"}
    )
    assert grounded.canonical_content() != plan.canonical_content()
    assert "recon:abc:modify" in grounded.canonical_content()


# ── serialization round-trip with a RepoMap attached ────────────────────

def test_plan_json_round_trip_with_repo_map():
    plan = _plan().model_copy(
        update={"repo_map": RepoMap(available=True, repo="o/r", languages=["go"])}
    )
    restored = NormalizedPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.repo_map is not None and restored.repo_map.repo == "o/r"
