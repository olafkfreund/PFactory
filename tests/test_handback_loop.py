"""Tests for the bounded closed-loop decision (P6 / #187)."""

from __future__ import annotations

import json

from agents.handback.loop import (
    decide_loop,
    failure_signature,
    max_cycles,
    read_loop_state,
    record_cycle,
)

# ── failure_signature ────────────────────────────────────────────────────


def test_failure_signature_is_reject_ids_only() -> None:
    verdicts = {
        "verdicts": [
            {"test_id": "a", "verdict": "accept"},
            {"test_id": "b", "verdict": "reject"},
            {"test_id": "c", "verdict": "flag"},
            {"test_id": "d", "verdict": "reject"},
        ]
    }
    assert failure_signature(verdicts) == {"b", "d"}


# ── decide_loop ──────────────────────────────────────────────────────────


def test_passed_when_no_failures() -> None:
    d = decide_loop(cycle=1, current_failures=set(), previous_failures={"x"}, cap=2)
    assert d.action == "passed"


def test_retest_when_failing_under_cap_and_progressing() -> None:
    d = decide_loop(cycle=0, current_failures={"a"}, previous_failures=None, cap=2)
    assert d.action == "retest"
    assert "1 failing" in d.reason


def test_stuck_at_cap() -> None:
    d = decide_loop(cycle=2, current_failures={"a"}, previous_failures={"b"}, cap=2)
    assert d.action == "stuck"
    assert "cap" in d.reason


def test_stuck_on_no_progress_same_failures() -> None:
    d = decide_loop(cycle=1, current_failures={"a", "b"}, previous_failures={"a", "b"}, cap=3)
    assert d.action == "stuck"
    assert "no progress" in d.reason


def test_progress_with_different_failures_is_retest() -> None:
    # fixed 'a' but uncovered 'c' — different set → still making progress.
    d = decide_loop(cycle=1, current_failures={"b", "c"}, previous_failures={"a", "b"}, cap=3)
    assert d.action == "retest"


def test_cap_takes_precedence_over_no_progress() -> None:
    d = decide_loop(cycle=2, current_failures={"a"}, previous_failures={"a"}, cap=2)
    assert d.action == "stuck"


# ── max_cycles env ───────────────────────────────────────────────────────


def test_max_cycles_default_and_override(monkeypatch) -> None:
    monkeypatch.delenv("PFACTORY_HANDBACK_MAX_CYCLES", raising=False)
    assert max_cycles() == 2
    monkeypatch.setenv("PFACTORY_HANDBACK_MAX_CYCLES", "5")
    assert max_cycles() == 5
    monkeypatch.setenv("PFACTORY_HANDBACK_MAX_CYCLES", "garbage")
    assert max_cycles() == 2  # falls back
    monkeypatch.setenv("PFACTORY_HANDBACK_MAX_CYCLES", "0")
    assert max_cycles() == 2  # non-positive ignored


# ── state persistence ────────────────────────────────────────────────────


def _seed_source(tmp_path, **extra):
    ctx = tmp_path / "context"
    ctx.mkdir(parents=True)
    (ctx / "source.json").write_text(
        json.dumps({"project_id": "demo", "correction_cycle": 0, **extra})
    )


def test_read_loop_state_seed(tmp_path) -> None:
    _seed_source(tmp_path)
    cycle, sig = read_loop_state(tmp_path)
    assert cycle == 0 and sig is None


def test_record_then_read_round_trip(tmp_path) -> None:
    _seed_source(tmp_path)
    record_cycle(tmp_path, cycle=1, failure_signature={"b", "a"})
    cycle, sig = read_loop_state(tmp_path)
    assert cycle == 1
    assert sig == {"a", "b"}
    # Existing keys preserved + signature stored sorted.
    data = json.loads((tmp_path / "context" / "source.json").read_text())
    assert data["project_id"] == "demo"
    assert data["last_failure_signature"] == ["a", "b"]


def test_read_loop_state_missing_source_is_safe(tmp_path) -> None:
    assert read_loop_state(tmp_path) == (0, None)
