"""Coverage-delta primitive — Task 7 (#8) commit 2.

One of the FIVE evaluation signals the Evaluator (commit 5) will use
to score each generated test:

  coverage delta · 3× stability re-run · LLM semantic relevance ·
  mutate-and-check probe · flake-lint promotion

This module is *pure compute* — it consumes two Cobertura coverage
XML files (the format the Executor's DockerRunner already emits via
``--cov-report=xml``) and returns the set of lines newly covered by
running the second test on top of the first.

The Evaluator commit-5 wiring will:
  1. Read a baseline coverage XML (whole-project coverage from the
     base branch, or coverage from running the project's existing
     test suite — pluggable).
  2. For each generated test under spec_dir/tests/ run by the
     Executor, read its individual coverage XML.
  3. Call ``compute_delta(baseline_xml, after_xml)`` to get a
     ``CoverageDelta`` per generated test.

A weak test that exercises no new code paths gets a delta with
``new_lines == set()`` and ``delta_pct == 0.0`` — the Evaluator's
verdict logic uses this as one of its rejection signals.

Schema reference: Cobertura XML
  https://cobertura.github.io/cobertura/
  Top-level <coverage line-rate="0.83" branch-rate="0.6" ...>
    <packages><package><classes>
      <class filename="app/auth/login.py" name="login">
        <lines>
          <line number="1" hits="1"/>
          <line number="2" hits="0"/>
          ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


class CoverageParseError(ValueError):
    """Raised when the Cobertura XML cannot be parsed."""


@dataclass(frozen=True)
class CoverageSnapshot:
    """The subset of Cobertura coverage we actually need.

    ``covered_lines`` maps each source file (path relative to the
    project root, as Cobertura emits it) to the set of line numbers
    that ran at least once (``hits >= 1``).

    ``line_rate`` is the top-level ``line-rate`` attribute — float
    in [0.0, 1.0]. Used for the ``delta_pct`` computation.
    """

    covered_lines: dict[str, frozenset[int]] = field(default_factory=dict)
    line_rate: float = 0.0
    total_lines: int = 0

    @property
    def total_covered(self) -> int:
        return sum(len(lines) for lines in self.covered_lines.values())


@dataclass(frozen=True)
class CoverageDelta:
    """The output of ``compute_delta(baseline, after)``.

    ``new_lines`` is the set of (file, line_number) pairs covered by
    ``after`` that were NOT covered by ``baseline``. An empty set
    means the test exercised no new code paths and is a candidate
    for rejection.

    ``new_files`` is the count of files whose coverage went from
    zero lines covered to one or more. Helps distinguish "deepened
    existing coverage" from "exercised a whole new module".

    ``delta_pct`` is ``after.line_rate - baseline.line_rate``,
    expressed as percentage points. Useful for the Evaluator's
    verdict prompt — the LLM gets the numeric delta to reason about.
    """

    new_lines: frozenset[tuple[str, int]] = field(default_factory=frozenset)
    new_files: int = 0
    delta_pct: float = 0.0
    baseline_total_covered: int = 0
    after_total_covered: int = 0

    @property
    def has_delta(self) -> bool:
        """True if the test added at least one line of coverage."""
        return len(self.new_lines) > 0


# ─── Parser ─────────────────────────────────────────────────────────────


def parse_coverage_xml(path: Path) -> CoverageSnapshot:
    """Parse a Cobertura coverage XML into the snapshot we need.

    Raises:
        FileNotFoundError: if ``path`` doesn't exist.
        CoverageParseError: if the file isn't valid Cobertura XML.
    """
    if not path.exists():
        raise FileNotFoundError(f"coverage XML not found: {path}")

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise CoverageParseError(
            f"failed to parse coverage XML at {path}: {exc}"
        ) from exc

    root = tree.getroot()
    if root.tag != "coverage":
        raise CoverageParseError(
            f"expected <coverage> root, found <{root.tag}> at {path}"
        )

    line_rate = float(root.attrib.get("line-rate", "0") or "0")
    covered_lines: dict[str, set[int]] = {}
    total_lines = 0

    # Cobertura nests: coverage > packages > package > classes > class > lines
    for class_el in root.iter("class"):
        filename = class_el.attrib.get("filename")
        if not filename:
            continue
        file_lines = covered_lines.setdefault(filename, set())
        for line_el in class_el.iter("line"):
            try:
                number = int(line_el.attrib["number"])
                hits = int(line_el.attrib.get("hits", "0"))
            except (KeyError, ValueError):
                # Malformed line entry — skip rather than fail the
                # whole parse; coverage.py occasionally emits stragglers.
                continue
            total_lines += 1
            if hits >= 1:
                file_lines.add(number)

    return CoverageSnapshot(
        covered_lines={k: frozenset(v) for k, v in covered_lines.items()},
        line_rate=line_rate,
        total_lines=total_lines,
    )


# ─── Compute ────────────────────────────────────────────────────────────


def compute_delta(
    baseline: CoverageSnapshot,
    after: CoverageSnapshot,
) -> CoverageDelta:
    """Return what ``after`` covered that ``baseline`` did not.

    Pure compute — same baseline + after always yields the same delta.

    Args:
        baseline: Coverage snapshot WITHOUT the generated test run.
        after: Coverage snapshot WITH the generated test included.

    Returns:
        CoverageDelta capturing the per-file/per-line additions plus
        the aggregate line-rate movement.
    """
    new_lines: set[tuple[str, int]] = set()
    new_files = 0

    for filename, after_lines in after.covered_lines.items():
        baseline_lines = baseline.covered_lines.get(filename, frozenset())
        added = after_lines - baseline_lines
        if added:
            new_lines.update((filename, ln) for ln in added)
            if not baseline_lines:
                # File went from completely uncovered to ≥1 line covered.
                new_files += 1

    delta_pct = (after.line_rate - baseline.line_rate) * 100.0

    return CoverageDelta(
        new_lines=frozenset(new_lines),
        new_files=new_files,
        delta_pct=delta_pct,
        baseline_total_covered=baseline.total_covered,
        after_total_covered=after.total_covered,
    )


def compute_delta_from_paths(
    baseline_path: Path,
    after_path: Path,
) -> CoverageDelta:
    """Convenience wrapper: parse both files then compute_delta.

    Same error contract as parse_coverage_xml on either side.
    """
    return compute_delta(
        parse_coverage_xml(baseline_path),
        parse_coverage_xml(after_path),
    )
