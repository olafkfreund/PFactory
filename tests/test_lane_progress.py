"""Unit tests for derive_lane_progress (#861)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "backend"))

from agents.lane_progress import derive_lane_progress


def _plan(tmp_path: Path, lanes: list[str]) -> None:
    (tmp_path / "test_plan.json").write_text(
        json.dumps({"phases": [{"subtasks": [{"lane": lane} for lane in lanes]}]})
    )


def test_triaged_uses_plan_lanes(tmp_path: Path) -> None:
    _plan(tmp_path, ["Unit", " api "])
    stored = {"unit": "pending", "e2e": "pending"}
    out = derive_lane_progress(tmp_path, {"status": "triaged", "lane_progress": stored})
    assert out == {"unit": "evaluated", "api": "evaluated", "e2e": "pending"}
    assert stored == {"unit": "pending", "e2e": "pending"}


def test_failed_without_plan_uses_stored_keys(tmp_path: Path) -> None:
    status = {"status": "planner_failed", "lane_progress": {"unit": "pending", "api": "pending"}}
    assert derive_lane_progress(tmp_path, status) == dict.fromkeys(
        ["unit", "api"], "planner_failed"
    )


def test_in_flight_status_keeps_stored(tmp_path: Path) -> None:
    assert (
        derive_lane_progress(tmp_path, {"status": "planning", "lane_progress": {"unit": "pending"}})
        is None
    )


def test_malformed_plan_falls_back_to_stored(tmp_path: Path) -> None:
    (tmp_path / "test_plan.json").write_text(json.dumps({"phases": [1, {"subtasks": ["x", {}]}]}))
    status = {"status": "evaluating", "lane_progress": {"unit": "pending"}}
    assert derive_lane_progress(tmp_path, status) == {"unit": "evaluating"}
    (tmp_path / "test_plan.json").write_text("not json")
    assert derive_lane_progress(tmp_path, status) == {"unit": "evaluating"}


def test_v01_lane_names_map_to_unit(tmp_path: Path) -> None:
    plan = {"phases": [{"subtasks": [{"lane": "functional"}, {"lane": "API"}]}]}
    (tmp_path / "test_plan.json").write_text(json.dumps(plan))
    status = {"status": "triaged", "lane_progress": {"unit": "pending"}}
    assert derive_lane_progress(tmp_path, status) == {"unit": "evaluated", "api": "evaluated"}
