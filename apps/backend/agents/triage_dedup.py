"""Triager dedup + rank primitives — Task 8 (#9) commit 2.

Two pure-compute primitives the Triager's commit-5 wiring will use:

  1. ``dedup_candidates`` — collapse byte-identical AND
     whitespace-only-different test files. The Gen-Functional agent
     can (and does, occasionally) emit two subtasks whose generated
     tests are reformulations of the same assertion. The Evaluator
     scores them independently; the Triager has to dedup before
     committing to git so the PR doesn't get duplicate tests.

  2. ``rank_candidates`` — deterministic ordering for the report.
     Verdict priority first (accept > flag > reject), then signal
     quality (killed > no_mutation > error mutation; stable > error
     stability), then coverage delta. test_id alphabetical breaks
     remaining ties.

This module is *pure compute* — no I/O, no clock, no env. The
Triager's commit-5 wiring will load verdicts.json + read each
generated test file, wrap them as ``TriageCandidate``s, and call
these primitives.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable

# ─── Data shapes ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TriageCandidate:
    """One test the Triager is considering.

    ``verdict`` carries the Evaluator's per-test verdict dict (the
    full structure from verdicts.json["verdicts"][i] — test_id,
    verdict, reasons, signals_summary, semantic_relevance, etc.).
    ``source`` is the raw test file contents (whitespace + all).
    """

    test_id: str
    test_file: str           # relative path, e.g., "tests/test_x.py"
    verdict: dict            # the verdicts.json entry, verbatim
    source: str              # the test file's literal text

    @property
    def verdict_label(self) -> str:
        """Read the bottom-line verdict string from the dict."""
        return self.verdict.get("verdict", "?")


@dataclass(frozen=True)
class DedupCollision:
    """A group of candidates whose test files matched on some hash.

    ``kind`` is either ``byte_identical`` or ``whitespace_normalised``
    — the latter is "same code modulo whitespace". The collision
    group's representative (the one that's kept) is at
    ``members[0]``; the rest are dropped.
    """

    kind: str                # 'byte_identical' | 'whitespace_normalised'
    representative: TriageCandidate
    dropped: tuple[TriageCandidate, ...]


@dataclass(frozen=True)
class DedupResult:
    """Output of ``dedup_candidates``.

    ``kept`` is the set of survivors in the SAME order they appeared
    in the input — preserving caller-controlled ordering of distinct
    tests (the rank step rearranges them afterwards).
    """

    kept: tuple[TriageCandidate, ...] = field(default_factory=tuple)
    collisions: tuple[DedupCollision, ...] = field(default_factory=tuple)


# ─── Hash primitives ────────────────────────────────────────────────────


def byte_hash(source: str) -> str:
    """SHA-256 of the raw UTF-8 bytes."""
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


_WHITESPACE_RUN = re.compile(r"[ \t]+")


def normalise_for_dedup(source: str) -> str:
    """Whitespace-normalise the source for the secondary dedup pass.

    Rules (intentionally conservative — never collapse semantically
    distinct lines):
      - CRLF → LF
      - Collapse runs of horizontal whitespace (space + tab) to a
        single space WITHIN a line
      - Strip trailing whitespace from each line
      - Drop entirely blank lines

    Note: leading whitespace (indentation) is preserved before
    collapse, then re-collapsed — but Python's indentation is
    *significant*. Two tests with different indentation levels would
    parse differently; we treat them as different. The collapse only
    affects the *length* of whitespace runs, not their presence.
    """
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    out_lines = []
    for line in text.split("\n"):
        # Preserve leading whitespace as a single tab-stop equivalent;
        # collapse anything past the first non-whitespace char.
        stripped = line.rstrip()
        if not stripped.strip():
            # blank or whitespace-only line — drop
            continue
        # Find the indent prefix (purely whitespace)
        i = 0
        while i < len(stripped) and stripped[i] in " \t":
            i += 1
        indent = stripped[:i]
        body = _WHITESPACE_RUN.sub(" ", stripped[i:])
        out_lines.append(indent + body)
    return "\n".join(out_lines) + "\n" if out_lines else ""


def normalised_hash(source: str) -> str:
    """SHA-256 of the whitespace-normalised source."""
    return hashlib.sha256(normalise_for_dedup(source).encode("utf-8")).hexdigest()


# ─── Dedup ──────────────────────────────────────────────────────────────


def dedup_candidates(candidates: Iterable[TriageCandidate]) -> DedupResult:
    """Two-pass dedup: byte-identical first, then whitespace-normalised.

    Preserves input order for the survivors. Collision groups carry
    the dropped members so the report can quote them.

    Empty input → empty result (no error).
    """
    cands = list(candidates)

    # Pass 1: byte-identical
    byte_groups: dict[str, list[TriageCandidate]] = {}
    for c in cands:
        byte_groups.setdefault(byte_hash(c.source), []).append(c)

    after_byte: list[TriageCandidate] = []
    collisions: list[DedupCollision] = []
    for group in byte_groups.values():
        if len(group) == 1:
            after_byte.append(group[0])
            continue
        after_byte.append(group[0])  # keep first occurrence
        collisions.append(DedupCollision(
            kind="byte_identical",
            representative=group[0],
            dropped=tuple(group[1:]),
        ))

    # Pass 2: whitespace-normalised (over the byte-pass survivors).
    norm_groups: dict[str, list[TriageCandidate]] = {}
    for c in after_byte:
        norm_groups.setdefault(normalised_hash(c.source), []).append(c)

    kept: list[TriageCandidate] = []
    # Preserve order from after_byte (already in input order).
    seen: set[str] = set()
    for c in after_byte:
        n = normalised_hash(c.source)
        if n in seen:
            continue
        seen.add(n)
        group = norm_groups[n]
        kept.append(group[0])
        if len(group) > 1:
            collisions.append(DedupCollision(
                kind="whitespace_normalised",
                representative=group[0],
                dropped=tuple(group[1:]),
            ))

    return DedupResult(
        kept=tuple(kept),
        collisions=tuple(collisions),
    )


# ─── Ranking ────────────────────────────────────────────────────────────


# Lower number = better (sorts first).
_VERDICT_PRIORITY = {"accept": 0, "flag": 1, "reject": 2}

# Lower number = better mutation signal.
_MUTATION_PRIORITY = {
    "killed": 0,
    "no_mutation": 1,
    "error": 2,
    "survived": 3,  # shouldn't reach Triager (Evaluator rejects them),
                    # but if it slips through, last in rank
}

# Lower number = better stability signal.
_STABILITY_PRIORITY = {
    "stable": 0,
    "no_mutation": 1,  # unreachable here; included for resilience
    "error": 2,
    "flaky": 3,
    "consistent_fail": 4,
}


def _coverage_delta_pct(c: TriageCandidate) -> float:
    """Extract delta_pct from the verdict's signals_summary, default 0.0."""
    summary = c.verdict.get("signals_summary") or {}
    val = summary.get("coverage_delta_pct")
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _mutation_label(c: TriageCandidate) -> str:
    summary = c.verdict.get("signals_summary") or {}
    return str(summary.get("mutation", "?")).lower()


def _stability_label(c: TriageCandidate) -> str:
    summary = c.verdict.get("signals_summary") or {}
    return str(summary.get("stability", "?")).lower()


def _rank_key(c: TriageCandidate) -> tuple:
    """Build the sort tuple.

    Sort ascending — lower tuple is "better" and lands earlier in
    the ranked list:
      1. verdict priority (accept=0, flag=1, reject=2; unknown=99)
      2. mutation priority (killed=0, etc.; unknown=99)
      3. stability priority (stable=0, etc.; unknown=99)
      4. -coverage_delta_pct  (higher is better → negate so ascending sort
         puts higher first)
      5. test_id  (alphabetical tie-breaker — deterministic)
    """
    return (
        _VERDICT_PRIORITY.get(c.verdict_label, 99),
        _MUTATION_PRIORITY.get(_mutation_label(c), 99),
        _STABILITY_PRIORITY.get(_stability_label(c), 99),
        -_coverage_delta_pct(c),
        c.test_id,
    )


def rank_candidates(
    candidates: Iterable[TriageCandidate],
) -> tuple[TriageCandidate, ...]:
    """Return candidates in deterministic rank order (best first).

    Pure function. Doesn't dedup — caller should dedup first if
    they want unique-source ranking.
    """
    return tuple(sorted(candidates, key=_rank_key))
