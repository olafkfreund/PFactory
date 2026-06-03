"""Tests for the Lane enum + Subtask.lane field — Task 3 (#4),
restructured in v0.2 Task 0 (#16).

Covers the v0.2 modality spine (UNIT/BROWSER/API/INTEGRATION/MUTATION),
v0.1 alias compatibility, Subtask defaults, round-trip via to_dict/
from_dict, and lifecycle preservation.
"""

from __future__ import annotations

import warnings

import pytest
from test_plan import (
    Lane,
    Subtask,
    SubtaskStatus,
)
from test_plan.enums import _parse_lane_str

# ── Lane enum (v0.2 spine) ──────────────────────────────────────────────


def test_lane_has_five_v02_values() -> None:
    assert {lane.value for lane in Lane} == {
        "unit", "browser", "api", "integration", "mutation",
    }


def test_lane_is_string_enum() -> None:
    # str subclass so it serialises naturally and JSON-encodes via .value
    assert isinstance(Lane.UNIT, str)
    assert Lane.UNIT == "unit"


def test_lane_browser_is_first_class() -> None:
    """Browser is the headline v0.2 lane (Decision 2)."""
    assert Lane.BROWSER.value == "browser"


# ── Subtask defaults ─────────────────────────────────────────────────────


def test_subtask_defaults_to_unit() -> None:
    """v0.2 default is UNIT (was FUNCTIONAL in v0.1)."""
    s = Subtask(id="1", description="write a test")
    assert s.lane == Lane.UNIT


def test_subtask_accepts_explicit_lane() -> None:
    s = Subtask(id="1", description="browser test", lane=Lane.BROWSER)
    assert s.lane == Lane.BROWSER


# ── Round-trip via to_dict / from_dict ───────────────────────────────────


def test_to_dict_emits_lane() -> None:
    s = Subtask(id="1", description="x", lane=Lane.API)
    assert s.to_dict()["lane"] == "api"


def test_from_dict_parses_lane() -> None:
    d = {"id": "1", "description": "x", "lane": "mutation"}
    s = Subtask.from_dict(d)
    assert s.lane == Lane.MUTATION


def test_from_dict_defaults_lane_when_missing() -> None:
    """JSON without a lane field round-trips as UNIT."""
    d = {"id": "1", "description": "x"}  # no lane key
    s = Subtask.from_dict(d)
    assert s.lane == Lane.UNIT


@pytest.mark.parametrize("lane", list(Lane))
def test_round_trip_all_lanes(lane: Lane) -> None:
    s = Subtask(id="1", description="x", lane=lane)
    again = Subtask.from_dict(s.to_dict())
    assert again.lane == lane


def test_from_dict_rejects_unknown_lane() -> None:
    with pytest.raises(ValueError):
        Subtask.from_dict({"id": "1", "description": "x", "lane": "nope"})


# ── v0.1 → v0.2 alias compatibility ────────────────────────────────────


@pytest.mark.parametrize("legacy_name,expected_lane", [
    ("functional", Lane.UNIT),
    ("sast",       Lane.UNIT),
    ("dast",       Lane.UNIT),
    ("fuzz",       Lane.UNIT),
])
def test_v01_alias_maps_with_warning(legacy_name: str, expected_lane: Lane) -> None:
    """v0.1 lane names parse to v0.2 lanes with a DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _parse_lane_str(legacy_name)
        assert result == expected_lane
        assert any(
            issubclass(w.category, DeprecationWarning) for w in caught
        ), f"no DeprecationWarning emitted for {legacy_name!r}"


def test_v01_alias_through_from_dict() -> None:
    """Old test_plan.json with lane='functional' loads as UNIT."""
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        s = Subtask.from_dict({"id": "1", "description": "x", "lane": "functional"})
    assert s.lane == Lane.UNIT


def test_v02_lane_names_emit_no_warning() -> None:
    """v0.2 names parse without a deprecation warning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _parse_lane_str("unit")
        _parse_lane_str("browser")
        _parse_lane_str("api")
        _parse_lane_str("integration")
        _parse_lane_str("mutation")
    deprecation = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecation == [], "v0.2 lane names should not warn"


# ── Status transitions still work with lane present ──────────────────────


def test_lifecycle_methods_preserve_lane() -> None:
    s = Subtask(id="1", description="x", lane=Lane.MUTATION)
    s.start(session_id=42)
    assert s.lane == Lane.MUTATION
    assert s.status == SubtaskStatus.IN_PROGRESS

    s.complete(output="ok")
    assert s.lane == Lane.MUTATION
    assert s.status == SubtaskStatus.COMPLETED


# ── Backwards compat ─────────────────────────────────────────────────────


def test_chunk_alias_is_subtask() -> None:
    """The Chunk backwards-compat alias must continue to exist."""
    from test_plan import Chunk
    assert Chunk is Subtask
