"""Tests for the RFC-0014 §4/§4b deterministic task scorer (#208).

Pure scoring: difficulty/risk from the contract signals, the routing class, and
the autonomy verdict (incl. the always-human hard overrides). No I/O.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytest.importorskip("pydantic")

from plan.emit.task_scorer import score_task  # noqa: E402


def _c(**kw):
    """A minimal contract dict with the given top-level/execution signals."""
    contract = {"final_acceptance": kw.pop("final_acceptance", [])}
    tier = kw.pop("tier", None)
    if tier is not None:
        contract["execution"] = {"autonomy_tier": tier}
    contract.update(kw)
    return contract


# ── difficulty ──────────────────────────────────────────────────────────────


def test_tier_drives_difficulty():
    assert score_task(_c(tier="low"))["difficulty"] == "low"
    assert score_task(_c(tier="medium"))["difficulty"] == "medium"
    assert score_task(_c(tier="hard"))["difficulty"] == "high"


def test_migration_floors_difficulty_high():
    s = score_task(_c(tier="low", change_mode="migration"))
    assert s["difficulty"] == "high"


def test_shape_bumps_low_to_medium():
    # 6+ ACs bumps a low task to at least medium.
    s = score_task(_c(tier="low", final_acceptance=["a"] * 6))
    assert s["difficulty"] == "medium"


def test_absent_tier_defaults_low():
    assert score_task(_c(final_acceptance=["a"]))["difficulty"] == "low"


# ── risk ────────────────────────────────────────────────────────────────────


def test_risk_from_deployment_class():
    s = score_task(_c(tier="low", deployment={"risk_class": "medium"}))
    assert s["risk"] == "medium"


def test_production_forces_high_risk():
    s = score_task(_c(tier="low", deployment={"production_classification": "production"}))
    assert s["risk"] == "high"


def test_destructive_iac_forces_high_risk():
    s = score_task(_c(tier="low", baseline={"blast_radius": {"destructive_iac": ["main.tf"]}}))
    assert s["risk"] == "high"


def test_security_scope_forces_high_risk():
    s = score_task(_c(tier="low", tfactory={"security_scope": ["sast"]}))
    assert s["risk"] == "high"


# ── routing class ─────────────────────────────────────────────────────────────


def test_routing_class_grid():
    assert score_task(_c(tier="low"))["routing_class"] == "economy"
    assert score_task(_c(tier="medium"))["routing_class"] == "standard"
    assert score_task(_c(tier="hard"))["routing_class"] == "premium"


def test_governed_overrides_class_on_production():
    s = score_task(_c(tier="medium", deployment={"production_classification": "production"}))
    assert s["routing_class"] == "governed"
    assert s["cost_ceiling"] == 25.0


# ── autonomy verdict (§4b) ───────────────────────────────────────────────────


def test_autonomous_only_low_low_no_override():
    s = score_task(_c(tier="low"))
    assert s["autonomy"] == "autonomous"
    assert "auto-merge" in s["reason"]


def test_review_for_medium():
    assert score_task(_c(tier="medium"))["autonomy"] == "review"


def test_approval_for_high():
    assert score_task(_c(tier="hard"))["autonomy"] == "approval"


@pytest.mark.parametrize(
    "contract",
    [
        {"deployment": {"production_classification": "production"}},
        {"baseline": {"blast_radius": {"destructive_iac": ["main.tf"]}}},
        {"change_mode": "migration"},
        {"tfactory": {"security_scope": ["sast"]}},
        {"access": {"requirements": [{"resource": "aws:s3"}]}},
    ],
)
def test_hard_overrides_force_approval_and_governed(contract):
    # Even a low-tier task with a hard override is forced to approval + governed.
    contract = {"execution": {"autonomy_tier": "low"}, "final_acceptance": [], **contract}
    s = score_task(contract)
    assert s["autonomy"] == "approval"
    assert s["routing_class"] == "governed"
    assert "override" in s["reason"]


def test_deterministic_repeatable():
    c = _c(tier="medium", deployment={"risk_class": "medium"}, final_acceptance=["a", "b"])
    assert score_task(c) == score_task(dict(c))
