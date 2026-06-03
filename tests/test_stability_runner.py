"""Tests for the 3× stability runner primitive — Task 7 (#8) commit 2.

The runner is mocked at the ``runner_fn`` seam — these tests verify
the verdict logic without spinning Docker. The integration with
docker_runner.DockerRunner.run_pytest happens in commit 5 of Task 7.

Covered:
  - All three runs exit 0 → STABLE
  - All three runs same non-zero code → CONSISTENT_FAIL
  - Mixed codes → FLAKY (0/1, 1/0/1, 1/2/3 — anything heterogeneous)
  - runner_fn raises → ERROR with message captured
  - Configurable rerun_count (e.g., 2, 5)
  - Invalid rerun_count rejected
  - Seed forwarded to runner_fn
  - stdout/stderr tail captured + truncated correctly
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from agents.stability_runner import (
    DEFAULT_SEED,
    RERUN_COUNT,
    StabilityResult,
    StabilityRun,
    StabilityVerdict,
    check_stability,
)

# ── Fake DockerRunResult ───────────────────────────────────────────────


@dataclass
class _FakeRunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


# ── Test fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def test_file(tmp_path: Path) -> Path:
    f = tmp_path / "test_x.py"
    f.write_text("def test_x(): pass\n")
    return f


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


# ── Verdict logic ──────────────────────────────────────────────────────


def test_all_three_pass_is_stable(test_file: Path, project_dir: Path) -> None:
    calls: list[int] = []
    def _runner(_tf, _pd, seed):
        calls.append(seed)
        return _FakeRunResult(returncode=0, stdout="passed")

    result = check_stability(test_file, project_dir, _runner)
    assert isinstance(result, StabilityResult)
    assert result.verdict == StabilityVerdict.STABLE
    assert result.is_acceptable is True
    assert len(result.runs) == 3
    assert all(r.ok for r in result.runs)
    assert calls == [DEFAULT_SEED] * 3


def test_all_three_fail_same_code_is_consistent(
    test_file: Path, project_dir: Path,
) -> None:
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=1, stdout="failed")

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.CONSISTENT_FAIL
    assert result.is_acceptable is False
    assert all(not r.ok for r in result.runs)


def test_mixed_pass_fail_is_flaky(test_file: Path, project_dir: Path) -> None:
    """Two passes + one fail → FLAKY (the most common real-world shape)."""
    sequence = iter([0, 1, 0])
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=next(sequence))

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.FLAKY
    assert result.is_acceptable is False
    codes = [r.returncode for r in result.runs]
    assert codes == [0, 1, 0]


def test_two_different_non_zero_codes_is_flaky(
    test_file: Path, project_dir: Path,
) -> None:
    """Different *kinds* of failure across runs are still FLAKY,
    not CONSISTENT_FAIL — the test isn't deterministically wrong,
    it's nondeterministically wrong."""
    sequence = iter([1, 2, 1])
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=next(sequence))

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.FLAKY


def test_runner_exception_is_error(test_file: Path, project_dir: Path) -> None:
    """If the runner itself blows up, verdict is ERROR — distinct from
    test failure. The Evaluator's verdict logic treats this as
    'inconclusive' (likely a sandbox issue, not the test's fault)."""
    def _runner(_tf, _pd, _seed):
        raise RuntimeError("docker socket missing")

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.ERROR
    assert result.is_acceptable is False
    assert "docker socket missing" in (result.error_message or "")
    assert "RuntimeError" in (result.error_message or "")
    # Runs collected up to the failing call are preserved (none here).
    assert result.runs == ()


def test_runner_exception_after_partial_runs(
    test_file: Path, project_dir: Path,
) -> None:
    """If the second run blows up, the first run's record is preserved."""
    sequence = iter([
        _FakeRunResult(returncode=0),
        # The next iteration raises StopIteration; we want a different
        # exception type to confirm it's captured.
    ])
    call_count = {"n": 0}
    def _runner(_tf, _pd, _seed):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeRunResult(returncode=0)
        raise ValueError("transient docker failure")

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.ERROR
    assert len(result.runs) == 1
    assert result.runs[0].ok is True
    assert "transient docker failure" in (result.error_message or "")


# ── Configurability ────────────────────────────────────────────────────


def test_rerun_count_two_allowed(test_file: Path, project_dir: Path) -> None:
    calls = []
    def _runner(_tf, _pd, _seed):
        calls.append(1)
        return _FakeRunResult(returncode=0)

    result = check_stability(test_file, project_dir, _runner, rerun_count=2)
    assert result.verdict == StabilityVerdict.STABLE
    assert len(result.runs) == 2
    assert result.rerun_count == 2


def test_rerun_count_five_runs_five_times(
    test_file: Path, project_dir: Path,
) -> None:
    calls = []
    def _runner(_tf, _pd, _seed):
        calls.append(1)
        return _FakeRunResult(returncode=0)

    result = check_stability(test_file, project_dir, _runner, rerun_count=5)
    assert len(calls) == 5
    assert len(result.runs) == 5


def test_rerun_count_one_rejected(test_file: Path, project_dir: Path) -> None:
    """A single run can't detect flake — must be at least 2."""
    def _runner(*a, **kw): return _FakeRunResult(returncode=0)
    with pytest.raises(ValueError, match="at least 2"):
        check_stability(test_file, project_dir, _runner, rerun_count=1)


def test_custom_seed_forwarded(test_file: Path, project_dir: Path) -> None:
    seen_seeds: list[int] = []
    def _runner(_tf, _pd, seed):
        seen_seeds.append(seed)
        return _FakeRunResult(returncode=0)

    result = check_stability(test_file, project_dir, _runner, seed=12345)
    assert seen_seeds == [12345, 12345, 12345]
    assert result.seed == 12345


def test_default_rerun_count_is_three() -> None:
    """The default of three is the documented convention; lock it down."""
    assert RERUN_COUNT == 3


# ── Output capture ─────────────────────────────────────────────────────


def test_stdout_tail_truncated(test_file: Path, project_dir: Path) -> None:
    """Long stdout is truncated to ``tail_chars`` to keep verdicts.json
    a reasonable size."""
    big_stdout = "x" * 10_000 + "TAIL_MARKER"
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=0, stdout=big_stdout)

    result = check_stability(test_file, project_dir, _runner, tail_chars=100)
    for r in result.runs:
        assert len(r.stdout_tail) == 100
        assert r.stdout_tail.endswith("TAIL_MARKER")


def test_stderr_tail_truncated(test_file: Path, project_dir: Path) -> None:
    big_stderr = "e" * 5_000 + "STDERR_TAIL"
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=1, stderr=big_stderr)

    result = check_stability(test_file, project_dir, _runner, tail_chars=50)
    for r in result.runs:
        assert len(r.stderr_tail) == 50
        assert r.stderr_tail.endswith("STDERR_TAIL")


def test_empty_stdout_stderr_handled(test_file: Path, project_dir: Path) -> None:
    """Some runners may return empty strings (or None — guarded)."""
    def _runner(_tf, _pd, _seed):
        return _FakeRunResult(returncode=0, stdout="", stderr="")

    result = check_stability(test_file, project_dir, _runner)
    assert result.verdict == StabilityVerdict.STABLE
    assert all(r.stdout_tail == "" for r in result.runs)
    assert all(r.stderr_tail == "" for r in result.runs)


# ── StabilityRun helper properties ─────────────────────────────────────


def test_stability_run_ok_property() -> None:
    assert StabilityRun(returncode=0).ok is True
    assert StabilityRun(returncode=1).ok is False
    assert StabilityRun(returncode=2).ok is False
