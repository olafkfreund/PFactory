"""Tests for the cross-run flaky-test history / flip-rate primitive (#37)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).parent.parent / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from agents.flaky_history import (  # noqa: E402
    FLAKY_THRESHOLD,
    HISTORY_WINDOW,
    FlakyClass,
    FlakyHistory,
    load_history,
    record_outcome,
)

# ── flip_rate maths ────────────────────────────────────────────────────

def test_flip_rate_zero_for_all_pass():
    h = FlakyHistory("t", (True, True, True, True))
    assert h.flip_rate == 0.0
    assert h.classification is FlakyClass.STABLE


def test_flip_rate_zero_for_all_fail():
    h = FlakyHistory("t", (False, False, False))
    assert h.flip_rate == 0.0
    assert h.classification is FlakyClass.STABLE


def test_flip_rate_alternating_is_one():
    h = FlakyHistory("t", (True, False, True, False))
    assert h.flip_rate == 1.0
    assert h.classification is FlakyClass.FLAKY


def test_flip_rate_single_flip():
    # one transition over four runs → 1/3
    h = FlakyHistory("t", (True, True, True, False))
    assert h.flip_rate == pytest.approx(1 / 3)
    assert h.classification is FlakyClass.FLAKY  # 0.333 >= 0.25


def test_flip_rate_below_threshold_is_stable():
    # one flip over five runs → 0.25 exactly is the threshold (flaky);
    # one flip over six runs → 0.2 < 0.25 → stable
    h = FlakyHistory("t", (True, True, True, True, True, False))
    assert h.flip_rate == pytest.approx(0.2)
    assert h.flip_rate < FLAKY_THRESHOLD
    assert h.classification is FlakyClass.STABLE


def test_threshold_boundary_is_flaky():
    # exactly FLAKY_THRESHOLD counts as flaky (>=)
    h = FlakyHistory("t", (True, False, True, True, True))  # 2 flips / 4 = 0.5
    assert h.flip_rate >= FLAKY_THRESHOLD
    assert h.classification is FlakyClass.FLAKY


# ── NEW classification ─────────────────────────────────────────────────

def test_empty_history_is_new():
    h = FlakyHistory("t", ())
    assert h.runs == 0
    assert h.flip_rate == 0.0
    assert h.classification is FlakyClass.NEW


def test_single_run_is_new():
    h = FlakyHistory("t", (True,))
    assert h.classification is FlakyClass.NEW


# ── store IO: load / record ────────────────────────────────────────────

def test_load_missing_store_returns_empty(tmp_path):
    h = load_history(tmp_path / "nope.json", "t1")
    assert h.outcomes == ()
    assert h.classification is FlakyClass.NEW


def test_record_creates_and_persists(tmp_path):
    store = tmp_path / "hist.json"
    h = record_outcome(store, "t1", True)
    assert h.outcomes == (True,)
    assert store.exists()
    saved = json.loads(store.read_text())
    assert saved["t1"]["outcomes"] == [True]


def test_record_appends_in_order(tmp_path):
    store = tmp_path / "hist.json"
    record_outcome(store, "t1", True)
    record_outcome(store, "t1", False)
    h = record_outcome(store, "t1", True)
    assert h.outcomes == (True, False, True)
    assert h.flip_rate == 1.0


def test_record_isolates_by_test_id(tmp_path):
    store = tmp_path / "hist.json"
    record_outcome(store, "t1", True)
    record_outcome(store, "t2", False)
    assert load_history(store, "t1").outcomes == (True,)
    assert load_history(store, "t2").outcomes == (False,)


def test_record_bounds_to_window(tmp_path):
    store = tmp_path / "hist.json"
    for _ in range(HISTORY_WINDOW + 5):
        h = record_outcome(store, "t1", True)
    assert h.runs == HISTORY_WINDOW
    assert load_history(store, "t1").runs == HISTORY_WINDOW


def test_record_respects_custom_window(tmp_path):
    store = tmp_path / "hist.json"
    for passed in (True, False, True, False, True):
        h = record_outcome(store, "t1", passed, window=3)
    assert h.outcomes == (True, False, True)
    assert h.runs == 3


def test_corrupt_store_is_tolerated(tmp_path):
    store = tmp_path / "hist.json"
    store.write_text("{ not json")
    # load returns empty rather than raising
    assert load_history(store, "t1").outcomes == ()
    # record recovers and writes a clean store
    h = record_outcome(store, "t1", True)
    assert h.outcomes == (True,)
    assert json.loads(store.read_text())["t1"]["outcomes"] == [True]


# ── as_dict surface (verdicts.json / triage report) ────────────────────

def test_as_dict_shape():
    h = FlakyHistory("ac1-login", (True, False, True))
    d = h.as_dict()
    assert d == {
        "test_id": "ac1-login",
        "runs": 3,
        "flip_rate": 1.0,
        "classification": "flaky",
    }


# ── triage-report integration (#37) ────────────────────────────────────


def test_triage_report_surfaces_flip_rate(tmp_path):
    """build_report reads the project-level history store and the markdown
    renderer shows the per-test flip-rate."""
    from agents.triage_dedup import TriageCandidate
    from agents.triage_report import build_report, render_markdown

    # Store lives at <workspace>/<project>/test_history.json — one level
    # above the spec dir.
    project_dir = tmp_path / "proj"
    spec_dir = project_dir / "specs" / "spec-1"
    spec_dir.mkdir(parents=True)
    store = project_dir / "test_history.json"

    # A chronically flaky test: alternating pass/fail across prior runs.
    for passed in (True, False, True, False):
        record_outcome(store, "ac1-login", passed)

    cand = TriageCandidate(
        test_id="ac1-login",
        test_file="tests/test_login.py",
        verdict={
            "test_id": "ac1-login",
            "verdict": "flag",
            "reasons": [],
            "signals_summary": {"coverage_delta_pct": 1.0,
                                "stability": "stable", "mutation": "killed"},
            "semantic_relevance": "high",
        },
        source="def test_x(): pass\n",
    )

    report = build_report(
        mode="dry-run",
        generated_at="2026-05-30T00:00:00Z",
        committed=(),
        flagged=(cand,),
        rejected=(),
        collisions=(),
        dedup_input_count=1,
        spec_dir=spec_dir,
    )

    assert report.flaky_by_test_id["ac1-login"]["classification"] == "flaky"
    assert report.flaky_by_test_id["ac1-login"]["flip_rate"] == 1.0

    md = render_markdown(report)
    assert "flaky history: flaky" in md
    assert "flip_rate=1.00 over 4 runs" in md

