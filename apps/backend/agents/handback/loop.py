"""Bounded closed-loop decision for the AIFactory hand-back (#187, epic #182).

After PFactory hands a correction to AIFactory and AIFactory's QA Fixer runs,
PFactory re-tests. This module is the *decision* half of that loop — pure and
unit-testable — while the polling + ``task_rerun`` orchestration lives in the
``/pfactory-fixloop`` skill (driven by ``/loop``), mirroring how
``/pfactory-watch`` keeps its verdict logic testable and its polling in the
skill.

The loop is bounded the same way the Planner's replan loop is
(``replan_count >= 2 → stuck``):

  - **passed** — the latest run has no failing tests; the feature is fixed.
  - **stuck**  — the correction-cycle cap is reached, OR the same tests still
    fail after a correction (no progress). A human takes over.
  - **retest** — there are failures and we're under the cap and making progress:
    hand back again + re-test.

Loop state (``correction_cycle`` + ``last_failure_signature``) is persisted in
``context/source.json`` alongside the cycle counter seeded by the snapshotter
(P1).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "LoopDecision",
    "decide_loop",
    "failure_signature",
    "max_cycles",
    "read_loop_state",
    "record_cycle",
]

# Mirrors the Planner's replan cap. Override with PFACTORY_HANDBACK_MAX_CYCLES.
_DEFAULT_MAX_CYCLES = 2

# The verdict label that counts as a failing test (kept in step with
# ``request._FAILING_VERDICTS``).
_FAILING_VERDICTS = frozenset({"reject"})


@dataclass(frozen=True)
class LoopDecision:
    action: str  # "passed" | "stuck" | "retest"
    reason: str
    cycle: int

    def to_dict(self) -> dict:
        return {"action": self.action, "reason": self.reason, "cycle": self.cycle}


def max_cycles() -> int:
    """The correction-cycle cap. Env-tunable; defaults to 2."""
    raw = os.environ.get("PFACTORY_HANDBACK_MAX_CYCLES")
    if raw is None:
        return _DEFAULT_MAX_CYCLES
    try:
        val = int(raw)
    except ValueError:
        return _DEFAULT_MAX_CYCLES
    return val if val > 0 else _DEFAULT_MAX_CYCLES


def failure_signature(verdicts: dict) -> set[str]:
    """The set of failing test_ids in a run — the loop's progress fingerprint."""
    return {
        entry.get("test_id")
        for entry in verdicts.get("verdicts", []) or []
        if isinstance(entry, dict) and entry.get("verdict") in _FAILING_VERDICTS
    }


def decide_loop(
    *,
    cycle: int,
    current_failures: set[str],
    previous_failures: set[str] | None,
    cap: int | None = None,
) -> LoopDecision:
    """Decide the next loop action from the latest run's failing set.

    Args:
        cycle: how many correction cycles have already run for this spec.
        current_failures: failing test_ids in the latest run.
        previous_failures: failing test_ids from the prior run (``None`` on the
            first pass — no correction has happened yet).
        cap: correction-cycle cap (defaults to ``max_cycles()``).
    """
    limit = max_cycles() if cap is None else cap

    if not current_failures:
        return LoopDecision("passed", "no failing tests remain", cycle)

    if cycle >= limit:
        return LoopDecision("stuck", f"reached the correction-cycle cap ({limit})", cycle)

    if previous_failures is not None and current_failures == previous_failures:
        return LoopDecision(
            "stuck",
            "the same tests still fail after a correction (no progress)",
            cycle,
        )

    n = len(current_failures)
    return LoopDecision("retest", f"{n} failing test(s) — hand back + re-test", cycle)


def _source_path(spec_dir: Path | str) -> Path:
    return Path(spec_dir) / "context" / "source.json"


def read_loop_state(spec_dir: Path | str) -> tuple[int, set[str] | None]:
    """Read ``(correction_cycle, last_failure_signature)`` from source.json.

    Returns ``(0, None)`` when source.json is missing/unreadable or the loop
    has not run yet.
    """
    try:
        data = json.loads(_source_path(spec_dir).read_text())
    except (OSError, ValueError):
        return 0, None
    cycle = int(data.get("correction_cycle") or 0)
    sig = data.get("last_failure_signature")
    return cycle, (set(sig) if isinstance(sig, list) else None)


def record_cycle(spec_dir: Path | str, *, cycle: int, failure_signature: set[str]) -> None:
    """Persist the new cycle count + failing signature into source.json."""
    path = _source_path(spec_dir)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    data["correction_cycle"] = cycle
    data["last_failure_signature"] = sorted(failure_signature)
    path.write_text(json.dumps(data, indent=2))
