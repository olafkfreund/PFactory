"""3× stability re-run primitive — Task 7 (#8) commit 2.

One of the FIVE evaluation signals the Evaluator (commit 5) will use:

  coverage delta · 3× stability re-run · LLM semantic relevance ·
  mutate-and-check probe · flake-lint promotion

A flaky test is one whose pass/fail status changes between identical
runs with the same seed. The 3× re-run is the cheapest cross-check
that catches the most common flake sources (timing, ordering,
non-deterministic data structures).

Verdict logic (all three runs against the same test file, same seed):

  STABLE          — all three runs exit 0
  FLAKY           — runs disagree (mix of pass/fail OR pass/error)
  CONSISTENT_FAIL — all three runs exit non-zero with the same code

The Evaluator commit-5 wiring will:
  1. For each generated test that passed the Executor's first run,
     call ``check_stability(test_file, project_dir, runner=docker_runner)``.
  2. Pass the resulting StabilityVerdict to the verdict-assembly
     prompt (FLAKY → reject, CONSISTENT_FAIL → reject, STABLE → ok).

This module exposes the docker_runner as an injected seam
(``runner_fn``) so unit tests can verify the orchestration without
spinning Docker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol

# How many times to re-run. Three is the smallest N that can yield
# all four verdict outcomes (stable, flaky, consistent_fail, error)
# without arbitrary-tie-breaking.
RERUN_COUNT = 3

# Default fixed seed for pytest-randomly (or PYTHONHASHSEED) — same
# seed across all three runs means a deterministic test stays
# deterministic. The seed is intentionally a non-zero, non-1 value
# so it's distinguishable from "unset" or "default".
DEFAULT_SEED = 424242


class StabilityVerdict(str, Enum):
    """Outcome of a 3× re-run."""

    STABLE = "stable"
    FLAKY = "flaky"
    CONSISTENT_FAIL = "consistent_fail"
    ERROR = "error"  # the runner itself raised, not a test failure


class _RunResultLike(Protocol):
    """Duck-type for DockerRunResult — keeps this module decoupled
    from the docker_runner import for circular-import safety."""

    @property
    def returncode(self) -> int: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...


@dataclass(frozen=True)
class StabilityRun:
    """One of N re-runs in a stability check."""

    returncode: int
    stdout_tail: str = ""
    stderr_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class StabilityResult:
    """Aggregate verdict + per-run record from ``check_stability``."""

    verdict: StabilityVerdict
    runs: tuple[StabilityRun, ...] = field(default_factory=tuple)
    seed: int = DEFAULT_SEED
    rerun_count: int = RERUN_COUNT
    error_message: str | None = None

    @property
    def is_acceptable(self) -> bool:
        """Convenience: did the test prove itself stable?"""
        return self.verdict == StabilityVerdict.STABLE


# ─── Public entrypoint ──────────────────────────────────────────────────


def check_stability(
    test_file: Path,
    project_dir: Path,
    runner_fn: Callable[[Path, Path, int], _RunResultLike],
    *,
    seed: int = DEFAULT_SEED,
    rerun_count: int = RERUN_COUNT,
    tail_chars: int = 500,
) -> StabilityResult:
    """Re-run ``test_file`` ``rerun_count`` times via ``runner_fn``;
    return a stability verdict.

    Args:
        test_file: Absolute path to the pytest file to re-run.
        project_dir: Project root the runner will mount (forwarded
            to ``runner_fn``).
        runner_fn: Callable that invokes the actual test runner. Sig:
            ``runner_fn(test_file, project_dir, seed) -> RunResultLike``.
            The Evaluator commit-5 wiring will pass a thin wrapper
            around docker_runner.DockerRunner.run_pytest; unit tests
            pass a fixture. Keeping this as a seam means we don't
            import docker_runner here (no circular risk).
        seed: Pytest seed passed to all 3 runs (same seed every time
            to keep determinism honest).
        rerun_count: How many times to re-run. Default 3.
        tail_chars: How many trailing chars of stdout/stderr to
            retain per run. Default 500 — enough to see a failure
            without blowing up the verdicts.json.

    Returns:
        StabilityResult capturing the verdict + per-run records.

    Raises:
        ValueError: if rerun_count < 2 (single-run can't detect flake).

    The runner_fn must not raise; any exception there bubbles up as
    a StabilityResult(verdict=ERROR, error_message=...).
    """
    if rerun_count < 2:
        raise ValueError(
            f"rerun_count must be at least 2 to detect flake; got {rerun_count}"
        )

    runs: list[StabilityRun] = []
    for _ in range(rerun_count):
        try:
            res = runner_fn(test_file, project_dir, seed)
        except Exception as exc:  # noqa: BLE001 — bubble runner errors up
            return StabilityResult(
                verdict=StabilityVerdict.ERROR,
                runs=tuple(runs),
                seed=seed,
                rerun_count=rerun_count,
                error_message=f"{type(exc).__name__}: {exc}"[:500],
            )
        runs.append(StabilityRun(
            returncode=res.returncode,
            stdout_tail=(res.stdout or "")[-tail_chars:],
            stderr_tail=(res.stderr or "")[-tail_chars:],
        ))

    codes = {r.returncode for r in runs}
    if codes == {0}:
        verdict = StabilityVerdict.STABLE
    elif len(codes) == 1:
        # All non-zero, all the same code → consistent failure.
        verdict = StabilityVerdict.CONSISTENT_FAIL
    else:
        # Mix of codes (e.g., 0 and 1, or 1 and 2) → flake.
        verdict = StabilityVerdict.FLAKY

    return StabilityResult(
        verdict=verdict,
        runs=tuple(runs),
        seed=seed,
        rerun_count=rerun_count,
    )
