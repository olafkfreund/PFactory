"""Tests for the Triager dedup + rank primitives — Task 8 (#9) commit 2.

Pure-compute primitives. The Triager's commit-5 wiring will load
verdicts.json + each generated test file, wrap them as
``TriageCandidate``s, and call ``dedup_candidates`` →
``rank_candidates``.

Covered:
  - byte_hash: deterministic + sensitive to single-char changes
  - normalise_for_dedup: CRLF→LF, blank-line drop, intra-line
    whitespace collapse, leading-indent preserved
  - normalised_hash: ignores whitespace-only edits, catches
    semantic differences
  - dedup_candidates: byte-identical pass, whitespace-only pass,
    multiple collision groups, preserves input order, empty input
  - rank_candidates: verdict priority, mutation priority, stability
    priority, coverage_delta_pct (higher is better), test_id
    alphabetical tie-breaker, missing-signal defaults
"""

from __future__ import annotations

import textwrap

import pytest
from agents.triage_dedup import (
    DedupCollision,
    DedupResult,
    TriageCandidate,
    byte_hash,
    dedup_candidates,
    normalise_for_dedup,
    normalised_hash,
    rank_candidates,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _cand(
    *, test_id: str, source: str, verdict: str = "accept",
    coverage_pct: float = 0.0, mutation: str = "killed",
    stability: str = "stable",
) -> TriageCandidate:
    return TriageCandidate(
        test_id=test_id,
        test_file=f"tests/test_{test_id}.py",
        verdict={
            "test_id": test_id,
            "verdict": verdict,
            "signals_summary": {
                "coverage_delta_pct": coverage_pct,
                "mutation": mutation,
                "stability": stability,
            },
        },
        source=source,
    )


# ── byte_hash ──────────────────────────────────────────────────────────


def test_byte_hash_is_deterministic() -> None:
    src = "def test_x(): assert 1\n"
    assert byte_hash(src) == byte_hash(src)


def test_byte_hash_sensitive_to_single_char_change() -> None:
    a = "def test_x(): assert 1\n"
    b = "def test_x(): assert 2\n"
    assert byte_hash(a) != byte_hash(b)


def test_byte_hash_sensitive_to_whitespace() -> None:
    """Byte hash must NOT collapse whitespace — that's the
    normalised hash's job."""
    a = "def test_x():\n    assert 1\n"
    b = "def test_x():\n  assert 1\n"  # different indent
    assert byte_hash(a) != byte_hash(b)


# ── normalise_for_dedup ────────────────────────────────────────────────


def test_normalise_crlf_to_lf() -> None:
    a = "line1\r\nline2\r\n"
    b = "line1\nline2\n"
    assert normalise_for_dedup(a) == normalise_for_dedup(b)


def test_normalise_drops_blank_lines() -> None:
    a = "line1\n\n\nline2\n"
    b = "line1\nline2\n"
    assert normalise_for_dedup(a) == normalise_for_dedup(b)


def test_normalise_collapses_intra_line_whitespace() -> None:
    a = "assert  x   ==     1"
    b = "assert x == 1"
    assert normalise_for_dedup(a) == normalise_for_dedup(b)


def test_normalise_preserves_indentation_structure() -> None:
    """Different indent levels must NOT normalise to the same value."""
    a = "def f():\n    return 1\n"
    b = "def f():\n  return 1\n"  # 2 vs 4 spaces
    assert normalise_for_dedup(a) != normalise_for_dedup(b)


def test_normalise_strips_trailing_whitespace() -> None:
    a = "x = 1   \n"
    b = "x = 1\n"
    assert normalise_for_dedup(a) == normalise_for_dedup(b)


def test_normalise_empty_input() -> None:
    assert normalise_for_dedup("") == ""
    assert normalise_for_dedup("\n\n\n") == ""


# ── normalised_hash ────────────────────────────────────────────────────


def test_normalised_hash_ignores_blank_lines() -> None:
    a = "def test_x():\n\n    assert 1\n"
    b = "def test_x():\n    assert 1\n"
    assert normalised_hash(a) == normalised_hash(b)


def test_normalised_hash_catches_semantic_difference() -> None:
    a = "def test_x():\n    assert 1\n"
    b = "def test_x():\n    assert 2\n"
    assert normalised_hash(a) != normalised_hash(b)


# ── dedup_candidates: byte-identical pass ──────────────────────────────


def test_dedup_byte_identical_pair_keeps_first() -> None:
    src = "def test_x():\n    assert 1\n"
    c1 = _cand(test_id="a", source=src)
    c2 = _cand(test_id="b", source=src)

    result = dedup_candidates([c1, c2])
    assert isinstance(result, DedupResult)
    assert len(result.kept) == 1
    assert result.kept[0].test_id == "a"  # first wins

    assert len(result.collisions) == 1
    coll = result.collisions[0]
    assert coll.kind == "byte_identical"
    assert coll.representative.test_id == "a"
    assert len(coll.dropped) == 1
    assert coll.dropped[0].test_id == "b"


def test_dedup_distinct_byte_no_collisions() -> None:
    c1 = _cand(test_id="a", source="def test_x(): assert 1\n")
    c2 = _cand(test_id="b", source="def test_y(): assert 2\n")
    result = dedup_candidates([c1, c2])
    assert len(result.kept) == 2
    assert result.collisions == ()


def test_dedup_three_byte_identical_drops_two() -> None:
    src = "def test_x(): assert 1\n"
    c1 = _cand(test_id="a", source=src)
    c2 = _cand(test_id="b", source=src)
    c3 = _cand(test_id="c", source=src)
    result = dedup_candidates([c1, c2, c3])
    assert len(result.kept) == 1
    assert result.kept[0].test_id == "a"
    assert len(result.collisions) == 1
    assert {d.test_id for d in result.collisions[0].dropped} == {"b", "c"}


# ── dedup_candidates: whitespace-normalised pass ───────────────────────


def test_dedup_whitespace_only_difference_collapses() -> None:
    """Same code, different formatting → one survivor, one
    whitespace-normalised collision."""
    a = "def test_x():\n    assert x == 1\n"
    b = "def test_x():\n    assert x   ==   1\n"   # extra spaces
    c1 = _cand(test_id="a", source=a)
    c2 = _cand(test_id="b", source=b)
    result = dedup_candidates([c1, c2])
    assert len(result.kept) == 1
    assert result.kept[0].test_id == "a"
    assert len(result.collisions) == 1
    assert result.collisions[0].kind == "whitespace_normalised"


def test_dedup_blank_lines_only_difference_collapses() -> None:
    a = "def test_x():\n    assert 1\n"
    b = "def test_x():\n\n\n    assert 1\n"
    result = dedup_candidates([
        _cand(test_id="a", source=a),
        _cand(test_id="b", source=b),
    ])
    assert len(result.kept) == 1
    assert result.collisions[0].kind == "whitespace_normalised"


def test_dedup_indent_difference_NOT_collapsed() -> None:
    """Different indentation = different code (Python semantics).
    Must NOT collapse."""
    a = "def test_x():\n    assert 1\n"
    b = "def test_x():\n  assert 1\n"  # 2 spaces vs 4
    result = dedup_candidates([
        _cand(test_id="a", source=a),
        _cand(test_id="b", source=b),
    ])
    assert len(result.kept) == 2
    assert result.collisions == ()


# ── dedup: mixed + edge cases ──────────────────────────────────────────


def test_dedup_byte_and_whitespace_collisions_together() -> None:
    """Three candidates: two byte-identical, third whitespace-only different
    from them. Expected: 1 keeper, byte collision (1) + whitespace
    collision (1)."""
    raw = "def test_x():\n    assert 1\n"
    raw_extra_ws = "def test_x():\n    assert  1\n"

    c1 = _cand(test_id="a", source=raw)
    c2 = _cand(test_id="b", source=raw)            # byte-dup of a
    c3 = _cand(test_id="c", source=raw_extra_ws)   # whitespace-dup of a

    result = dedup_candidates([c1, c2, c3])
    assert len(result.kept) == 1
    assert result.kept[0].test_id == "a"
    assert len(result.collisions) == 2
    kinds = {coll.kind for coll in result.collisions}
    assert kinds == {"byte_identical", "whitespace_normalised"}


def test_dedup_preserves_input_order_of_survivors() -> None:
    """Three distinct tests, no collisions → kept order = input order."""
    c1 = _cand(test_id="z", source="def t(): assert 1\n")
    c2 = _cand(test_id="m", source="def t(): assert 2\n")
    c3 = _cand(test_id="a", source="def t(): assert 3\n")
    result = dedup_candidates([c1, c2, c3])
    ids = [c.test_id for c in result.kept]
    assert ids == ["z", "m", "a"]  # input order — rank step changes this


def test_dedup_empty_input() -> None:
    result = dedup_candidates([])
    assert result.kept == ()
    assert result.collisions == ()


# ── rank_candidates: verdict priority ──────────────────────────────────


def test_rank_accept_before_flag_before_reject() -> None:
    a = _cand(test_id="a", source="def t(): pass\n", verdict="reject")
    b = _cand(test_id="b", source="def t(): pass\n", verdict="flag")
    c = _cand(test_id="c", source="def t(): pass\n", verdict="accept")
    ranked = rank_candidates([a, b, c])
    assert [r.test_id for r in ranked] == ["c", "b", "a"]


# ── rank_candidates: mutation priority ─────────────────────────────────


def test_rank_killed_before_no_mutation_before_error() -> None:
    """All accept verdicts; mutation signal differs."""
    a = _cand(test_id="a", source="s\n", verdict="accept", mutation="error")
    b = _cand(test_id="b", source="s\n", verdict="accept", mutation="no_mutation")
    c = _cand(test_id="c", source="s\n", verdict="accept", mutation="killed")
    ranked = rank_candidates([a, b, c])
    assert [r.test_id for r in ranked] == ["c", "b", "a"]


# ── rank_candidates: stability priority ────────────────────────────────


def test_rank_stable_before_flaky_before_consistent_fail() -> None:
    """All accept + killed; stability differs."""
    a = _cand(test_id="a", source="s\n", verdict="accept", stability="consistent_fail")
    b = _cand(test_id="b", source="s\n", verdict="accept", stability="flaky")
    c = _cand(test_id="c", source="s\n", verdict="accept", stability="stable")
    ranked = rank_candidates([a, b, c])
    assert [r.test_id for r in ranked] == ["c", "b", "a"]


# ── rank_candidates: coverage delta ────────────────────────────────────


def test_rank_higher_coverage_delta_wins() -> None:
    """All other signals equal — coverage delta is the tiebreaker."""
    a = _cand(test_id="a", source="s\n", coverage_pct=1.0)
    b = _cand(test_id="b", source="s\n", coverage_pct=10.0)
    c = _cand(test_id="c", source="s\n", coverage_pct=5.0)
    ranked = rank_candidates([a, b, c])
    assert [r.test_id for r in ranked] == ["b", "c", "a"]


# ── rank_candidates: alphabetical tie-breaker ──────────────────────────


def test_rank_alphabetical_tiebreaker() -> None:
    """Identical signals → test_id alphabetical."""
    a = _cand(test_id="z-test", source="s\n")
    b = _cand(test_id="a-test", source="s\n")
    c = _cand(test_id="m-test", source="s\n")
    ranked = rank_candidates([a, b, c])
    assert [r.test_id for r in ranked] == ["a-test", "m-test", "z-test"]


# ── rank_candidates: missing signal handling ───────────────────────────


def test_rank_missing_signals_summary_defaults_safely() -> None:
    """Verdict dict with no signals_summary — must not crash."""
    c1 = TriageCandidate(
        test_id="missing",
        test_file="tests/test_missing.py",
        verdict={"test_id": "missing", "verdict": "accept"},  # no signals_summary
        source="s\n",
    )
    c2 = _cand(test_id="full", source="s\n", coverage_pct=5.0)
    ranked = rank_candidates([c1, c2])
    # 'full' has coverage_pct=5.0 (higher = better); 'missing' defaults to 0.0
    assert ranked[0].test_id == "full"
    assert ranked[1].test_id == "missing"


def test_rank_unknown_verdict_label_sorts_last() -> None:
    """Verdict value the matrix doesn't know about defaults to 99 priority."""
    a = _cand(test_id="a", source="s\n", verdict="unknown-future-state")
    b = _cand(test_id="b", source="s\n", verdict="accept")
    ranked = rank_candidates([a, b])
    assert [r.test_id for r in ranked] == ["b", "a"]


def test_rank_empty_input() -> None:
    assert rank_candidates([]) == ()
