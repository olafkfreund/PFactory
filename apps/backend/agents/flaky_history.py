"""Cross-run flaky-test history — flip-rate signal (#37).

The Evaluator's 3× ``stability_runner`` catches *in-run* flakiness (a test
that disagrees with itself across three identical re-runs). It cannot catch
a test that passes consistently within one pipeline run but flips between
*separate* runs — the kind of intermittent flake that only history reveals.

This module persists a per-test pass/fail history keyed by ``test_id`` and
derives a **flip-rate**: the fraction of consecutive runs whose outcome
changed. Research (FlakyLens, OOPSLA 2025) shows that historical flip-rate
is a far more reliable flakiness signal than any single-run heuristic, so
the Evaluator/Triager use it to down-rank chronically flaky tests.

Design mirrors the other signal primitives (``stability_runner`` etc.):

  - pure, dependency-light, frozen dataclasses
  - an injected ``store_path`` seam so unit tests never touch real
    workspace state
  - bounded, append-only outcome ring per test (``HISTORY_WINDOW``)

Store format (JSON at ``store_path``)::

    {"<test_id>": {"outcomes": [true, true, false, true]}}

``outcomes`` is chronological; ``true`` = the test passed that run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Keep the most-recent N outcomes per test. 30 is enough to surface a
# trend without letting ancient history dominate a test that has since
# stabilised (or rotted).
HISTORY_WINDOW = 30

# flip_rate at or above this is "flaky". 0.25 = one flip every four runs;
# a genuinely stable test sits at 0.0, a coin-flip test approaches 0.5.
FLAKY_THRESHOLD = 0.25

# Below this many recorded runs we can't judge flakiness — treat as NEW.
MIN_RUNS_FOR_VERDICT = 2


class FlakyClass(str, Enum):
    """Historical flakiness classification for a test."""

    NEW = "new"          # too few runs to judge
    STABLE = "stable"    # flip_rate below threshold
    FLAKY = "flaky"      # flip_rate at/above threshold


@dataclass(frozen=True)
class FlakyHistory:
    """A test's recorded pass/fail history + derived flip-rate.

    ``outcomes`` is chronological (oldest first); ``True`` == passed.
    """

    test_id: str
    outcomes: tuple[bool, ...] = ()

    @property
    def runs(self) -> int:
        return len(self.outcomes)

    @property
    def flip_rate(self) -> float:
        """Fraction of consecutive runs whose outcome changed.

        ``0.0`` for fewer than two runs (no transition is possible).
        """
        if self.runs < 2:
            return 0.0
        flips = sum(
            1 for a, b in zip(self.outcomes, self.outcomes[1:]) if a != b
        )
        return flips / (self.runs - 1)

    @property
    def classification(self) -> FlakyClass:
        if self.runs < MIN_RUNS_FOR_VERDICT:
            return FlakyClass.NEW
        return (
            FlakyClass.FLAKY
            if self.flip_rate >= FLAKY_THRESHOLD
            else FlakyClass.STABLE
        )

    def as_dict(self) -> dict[str, object]:
        """JSON-friendly summary for verdicts.json / triage report."""
        return {
            "test_id": self.test_id,
            "runs": self.runs,
            "flip_rate": round(self.flip_rate, 4),
            "classification": self.classification.value,
        }


def _read_store(store_path: Path) -> dict[str, dict]:
    """Return the raw store dict; empty if missing/corrupt."""
    if not store_path.exists():
        return {}
    try:
        data = json.loads(store_path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_store(store_path: Path, data: dict[str, dict]) -> None:
    """Persist the store atomically (write-temp-then-rename)."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = store_path.with_suffix(store_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(store_path)


def load_history(store_path: Path, test_id: str) -> FlakyHistory:
    """Return the recorded :class:`FlakyHistory` for *test_id* (empty if none)."""
    entry = _read_store(store_path).get(test_id, {})
    raw = entry.get("outcomes", []) if isinstance(entry, dict) else []
    outcomes = tuple(bool(x) for x in raw)
    return FlakyHistory(test_id=test_id, outcomes=outcomes)


def record_outcome(
    store_path: Path,
    test_id: str,
    passed: bool,
    *,
    window: int = HISTORY_WINDOW,
) -> FlakyHistory:
    """Append *passed* to *test_id*'s history, persist, and return the update.

    The per-test outcome list is bounded to the most-recent *window* entries.
    """
    data = _read_store(store_path)
    entry = data.get(test_id)
    prior = entry.get("outcomes", []) if isinstance(entry, dict) else []
    outcomes = [bool(x) for x in prior]
    outcomes.append(bool(passed))
    outcomes = outcomes[-window:]
    data[test_id] = {"outcomes": outcomes}
    _write_store(store_path, data)
    return FlakyHistory(test_id=test_id, outcomes=tuple(outcomes))
