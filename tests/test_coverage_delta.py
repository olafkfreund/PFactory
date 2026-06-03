"""Tests for the coverage-delta primitive — Task 7 (#8) commit 2.

Pure-compute primitive that the Evaluator (commit 5) will use to
decide whether a generated test exercises any new code paths. Unit-
tested in isolation here; the agent wiring lands in commit 5.

Covered:
  - parse_coverage_xml: happy path, line_rate, total_covered,
    malformed line entries skipped, missing file errors
  - compute_delta: zero delta (baseline ⊇ after), positive delta
    (new lines), new-file delta, multi-file aggregation
  - delta_pct math (line-rate movement → percentage points)
  - compute_delta_from_paths convenience wrapper
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from agents.coverage_delta import (
    CoverageDelta,
    CoverageParseError,
    CoverageSnapshot,
    compute_delta,
    compute_delta_from_paths,
    parse_coverage_xml,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _cobertura(line_rate: float, classes: list[tuple[str, list[tuple[int, int]]]]) -> str:
    """Build a minimal Cobertura XML.

    Args:
        line_rate: top-level line-rate attribute.
        classes: list of (filename, [(line_number, hits), ...]).
    """
    inner = []
    for filename, lines in classes:
        line_xml = "".join(
            f'<line number="{n}" hits="{h}"/>' for n, h in lines
        )
        inner.append(
            f'<class filename="{filename}" name="x"><lines>{line_xml}</lines></class>'
        )
    classes_xml = "".join(inner)
    return textwrap.dedent(f'''\
        <?xml version="1.0" ?>
        <coverage line-rate="{line_rate}" branch-rate="0" version="6.0" timestamp="1700000000">
          <packages>
            <package name="app" line-rate="{line_rate}" branch-rate="0">
              <classes>{classes_xml}</classes>
            </package>
          </packages>
        </coverage>
    ''')


# ── parse_coverage_xml ─────────────────────────────────────────────────


def test_parse_happy_path(tmp_path: Path) -> None:
    xml = _cobertura(0.50, [
        ("app/auth/login.py", [(1, 1), (2, 1), (3, 0)]),
        ("app/auth/session.py", [(10, 1), (11, 1)]),
    ])
    p = tmp_path / "coverage.xml"
    p.write_text(xml)

    snap = parse_coverage_xml(p)
    assert snap.line_rate == 0.50
    assert snap.total_lines == 5  # all 5 line entries
    assert snap.covered_lines["app/auth/login.py"] == frozenset({1, 2})
    assert snap.covered_lines["app/auth/session.py"] == frozenset({10, 11})
    assert snap.total_covered == 4


def test_parse_empty_coverage(tmp_path: Path) -> None:
    xml = _cobertura(0.0, [])
    p = tmp_path / "coverage.xml"
    p.write_text(xml)
    snap = parse_coverage_xml(p)
    assert snap.covered_lines == {}
    assert snap.line_rate == 0.0
    assert snap.total_covered == 0


def test_parse_skips_malformed_lines(tmp_path: Path) -> None:
    """A <line> without ``number`` is dropped, the rest survive."""
    xml = '''<?xml version="1.0"?>
    <coverage line-rate="0.5">
      <classes>
        <class filename="x.py" name="x"><lines>
          <line number="1" hits="1"/>
          <line hits="1"/>
          <line number="3" hits="abc"/>
          <line number="4" hits="1"/>
        </lines></class>
      </classes>
    </coverage>'''
    p = tmp_path / "coverage.xml"
    p.write_text(xml)
    snap = parse_coverage_xml(p)
    assert snap.covered_lines["x.py"] == frozenset({1, 4})


def test_parse_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_coverage_xml(tmp_path / "nope.xml")


def test_parse_invalid_xml_raises_coverageparseerror(tmp_path: Path) -> None:
    p = tmp_path / "bad.xml"
    p.write_text("not xml at all <<<")
    with pytest.raises(CoverageParseError):
        parse_coverage_xml(p)


def test_parse_wrong_root_element_raises(tmp_path: Path) -> None:
    p = tmp_path / "wrong.xml"
    p.write_text("<not-coverage/>")
    with pytest.raises(CoverageParseError, match="expected <coverage>"):
        parse_coverage_xml(p)


# ── compute_delta ──────────────────────────────────────────────────────


def test_delta_zero_when_after_subset_of_baseline() -> None:
    baseline = CoverageSnapshot(
        covered_lines={"x.py": frozenset({1, 2, 3})},
        line_rate=0.50, total_lines=6,
    )
    after = CoverageSnapshot(
        covered_lines={"x.py": frozenset({1, 2})},
        line_rate=0.33, total_lines=6,
    )
    delta = compute_delta(baseline, after)
    assert delta.new_lines == frozenset()
    assert delta.new_files == 0
    assert delta.has_delta is False
    # delta_pct is negative — test regressed coverage. That's allowed
    # by the primitive; the Evaluator's verdict logic decides what to do.
    assert delta.delta_pct < 0


def test_delta_positive_when_new_lines() -> None:
    baseline = CoverageSnapshot(
        covered_lines={"x.py": frozenset({1, 2})},
        line_rate=0.20, total_lines=10,
    )
    after = CoverageSnapshot(
        covered_lines={"x.py": frozenset({1, 2, 3, 4, 5})},
        line_rate=0.50, total_lines=10,
    )
    delta = compute_delta(baseline, after)
    assert delta.new_lines == frozenset({("x.py", 3), ("x.py", 4), ("x.py", 5)})
    assert delta.new_files == 0  # x.py was already partially covered
    assert delta.has_delta is True
    # 0.50 - 0.20 = 0.30 → 30 percentage points
    assert delta.delta_pct == pytest.approx(30.0)


def test_delta_new_file_counted_separately() -> None:
    baseline = CoverageSnapshot(
        covered_lines={"a.py": frozenset({1})},
        line_rate=0.20, total_lines=5,
    )
    after = CoverageSnapshot(
        covered_lines={
            "a.py": frozenset({1}),
            "b.py": frozenset({10, 11}),
        },
        line_rate=0.60, total_lines=5,
    )
    delta = compute_delta(baseline, after)
    assert delta.new_lines == frozenset({("b.py", 10), ("b.py", 11)})
    assert delta.new_files == 1
    assert delta.delta_pct == pytest.approx(40.0)


def test_delta_baseline_and_after_totals_recorded() -> None:
    baseline = CoverageSnapshot(
        covered_lines={"a.py": frozenset({1, 2})}, line_rate=0.4, total_lines=5,
    )
    after = CoverageSnapshot(
        covered_lines={"a.py": frozenset({1, 2, 3})}, line_rate=0.6, total_lines=5,
    )
    delta = compute_delta(baseline, after)
    assert delta.baseline_total_covered == 2
    assert delta.after_total_covered == 3


def test_delta_aggregates_across_many_files() -> None:
    baseline = CoverageSnapshot(
        covered_lines={
            "a.py": frozenset({1, 2}),
            "b.py": frozenset({10}),
        },
        line_rate=0.30, total_lines=10,
    )
    after = CoverageSnapshot(
        covered_lines={
            "a.py": frozenset({1, 2, 3}),  # +1
            "b.py": frozenset({10, 11}),   # +1
            "c.py": frozenset({20, 21}),   # whole new file
        },
        line_rate=0.60, total_lines=10,
    )
    delta = compute_delta(baseline, after)
    assert delta.new_lines == frozenset({
        ("a.py", 3), ("b.py", 11), ("c.py", 20), ("c.py", 21),
    })
    assert delta.new_files == 1  # only c.py is new


# ── compute_delta_from_paths ───────────────────────────────────────────


def test_compute_from_paths_roundtrip(tmp_path: Path) -> None:
    """End-to-end: two XML files → CoverageDelta via the public wrapper."""
    baseline_xml = _cobertura(0.20, [("x.py", [(1, 1), (2, 0)])])
    after_xml = _cobertura(0.60, [("x.py", [(1, 1), (2, 1), (3, 1)])])

    bp = tmp_path / "before.xml"
    ap = tmp_path / "after.xml"
    bp.write_text(baseline_xml)
    ap.write_text(after_xml)

    delta = compute_delta_from_paths(bp, ap)
    assert isinstance(delta, CoverageDelta)
    assert delta.new_lines == frozenset({("x.py", 2), ("x.py", 3)})
    assert delta.delta_pct == pytest.approx(40.0)


def test_compute_from_paths_missing_baseline(tmp_path: Path) -> None:
    """Convenience wrapper bubbles up FileNotFoundError unchanged."""
    after_xml = _cobertura(0.5, [("x.py", [(1, 1)])])
    ap = tmp_path / "after.xml"
    ap.write_text(after_xml)
    with pytest.raises(FileNotFoundError):
        compute_delta_from_paths(tmp_path / "missing.xml", ap)
