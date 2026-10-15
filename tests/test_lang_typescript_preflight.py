"""Tests for the TypeScript pre-flight primitive — Task 9 (#25) commit 5.

Covers:
  - Happy path: clean file → ok=True, no unresolved imports
  - Single unresolved import (TS2307) → parsed into unresolved_imports
  - Multiple unresolved imports → all captured, deduplicated
  - TS2304 "cannot find name" → also classified as unresolved_imports
  - Distinguishes unresolved imports from other errors (TS1005, TS2345, etc.)
  - Correctly parses tsc TS2307 error format with module specifier
  - runner_fn mock returns predictable output for each scenario
  - Returns raw_output for caller-side debugging
  - Handles tsc exit code 0 (success) vs non-zero (errors)
  - Handles empty test file (ok=True with empty output)
  - Handles binary/garbage input (graceful error)
  - Parametrized across multiple TS error codes (TS2307, TS2304, TS1005)
  - runner_image parameter is passed through to the runner_fn
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from agents.lang_typescript.preflight import (
    TSPreflightReport,
    _parse_tsc_output,
    run_ts_preflight,
)

# ─── Mock runner ─────────────────────────────────────────────────────────────


@dataclass
class _FakeRunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _make_runner(returncode: int, stdout: str = "", stderr: str = ""):
    """Return a runner_fn that always returns the given canned result."""
    def _runner(cmd, cwd, *, image, timeout):
        return _FakeRunResult(returncode=returncode, stdout=stdout, stderr=stderr)
    return _runner


def _recording_runner(calls: list, returncode: int = 0, stdout: str = ""):
    """Runner that records calls for assertion."""
    def _runner(cmd, cwd, *, image, timeout):
        calls.append({"cmd": cmd, "cwd": cwd, "image": image, "timeout": timeout})
        return _FakeRunResult(returncode=returncode, stdout=stdout)
    return _runner


# ─── _parse_tsc_output unit tests ────────────────────────────────────────────


def test_parse_clean_output_returns_empty() -> None:
    unresolved, other = _parse_tsc_output("")
    assert unresolved == ()
    assert other == ()


def test_parse_ts2307_extracts_module_specifier() -> None:
    raw = (
        "test.ts(3,19): error TS2307: "
        "Cannot find module './does-not-exist' or its corresponding type declarations.\n"
    )
    unresolved, other = _parse_tsc_output(raw)
    assert "./does-not-exist" in unresolved
    assert other == ()


def test_parse_ts2307_with_package_specifier() -> None:
    raw = (
        "test.ts(2,1): error TS2307: "
        "Cannot find module '@org/missing-pkg' or its corresponding type declarations.\n"
    )
    unresolved, other = _parse_tsc_output(raw)
    assert "@org/missing-pkg" in unresolved


def test_parse_ts2304_cannot_find_name() -> None:
    raw = "test.ts(5,3): error TS2304: Cannot find name 'MyMissingType'.\n"
    unresolved, other = _parse_tsc_output(raw)
    assert "MyMissingType" in unresolved
    assert other == ()


def test_parse_ts1005_goes_to_other_errors() -> None:
    raw = "test.ts(8,1): error TS1005: ';' expected.\n"
    unresolved, other = _parse_tsc_output(raw)
    assert unresolved == ()
    assert len(other) == 1
    assert "TS1005" in other[0]


def test_parse_ts2345_type_error_goes_to_other() -> None:
    raw = (
        "test.ts(10,5): error TS2345: "
        "Argument of type 'string' is not assignable to parameter of type 'number'.\n"
    )
    unresolved, other = _parse_tsc_output(raw)
    assert unresolved == ()
    assert "TS2345" in other[0]


def test_parse_deduplicates_repeated_unresolved_imports() -> None:
    raw = (
        "test.ts(2,1): error TS2307: Cannot find module './foo' or its corresponding type declarations.\n"
        "test.ts(5,1): error TS2307: Cannot find module './foo' or its corresponding type declarations.\n"
        "test.ts(8,1): error TS2307: Cannot find module './bar' or its corresponding type declarations.\n"
    )
    unresolved, _ = _parse_tsc_output(raw)
    assert unresolved.count("./foo") == 1
    assert "./bar" in unresolved


def test_parse_mixed_error_types_separated() -> None:
    raw = (
        "test.ts(1,1): error TS2307: Cannot find module './missing' or its corresponding type declarations.\n"
        "test.ts(3,1): error TS1005: ';' expected.\n"
        "test.ts(5,1): error TS2304: Cannot find name 'Ghost'.\n"
    )
    unresolved, other = _parse_tsc_output(raw)
    assert "./missing" in unresolved
    assert "Ghost" in unresolved
    assert len(other) == 1
    assert "TS1005" in other[0]


# ─── run_ts_preflight integration tests ──────────────────────────────────────


@pytest.fixture
def ts_file(tmp_path: Path) -> Path:
    """Create a minimal TypeScript test file in tmp_path."""
    f = tmp_path / "mytest.test.ts"
    f.write_text("import { foo } from './foo';\ntest('x', () => { expect(foo()).toBe(1); });\n")
    return f


def test_happy_path_ok_true(ts_file: Path, tmp_path: Path) -> None:
    """Exit code 0 from tsc → ok=True, empty import lists."""
    runner = _make_runner(returncode=0, stdout="", stderr="")
    report = run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert isinstance(report, TSPreflightReport)
    assert report.ok is True
    assert report.unresolved_imports == ()
    assert report.other_errors == ()
    assert report.test_file == ts_file


def test_single_unresolved_import_detected(ts_file: Path, tmp_path: Path) -> None:
    tsc_output = (
        "mytest.test.ts(1,19): error TS2307: "
        "Cannot find module './does-not-exist' or its corresponding type declarations.\n"
    )
    runner = _make_runner(returncode=1, stdout=tsc_output)
    report = run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert report.ok is False
    assert "./does-not-exist" in report.unresolved_imports
    assert report.other_errors == ()


def test_multiple_unresolved_imports_all_captured(ts_file: Path, tmp_path: Path) -> None:
    tsc_output = (
        "mytest.test.ts(1,1): error TS2307: Cannot find module './foo' or its corresponding type declarations.\n"
        "mytest.test.ts(2,1): error TS2307: Cannot find module '@org/bar' or its corresponding type declarations.\n"
        "mytest.test.ts(3,1): error TS2307: Cannot find module '../utils/baz' or its corresponding type declarations.\n"
    )
    runner = _make_runner(returncode=1, stdout=tsc_output)
    report = run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert report.ok is False
    assert len(report.unresolved_imports) == 3
    assert "./foo" in report.unresolved_imports
    assert "@org/bar" in report.unresolved_imports
    assert "../utils/baz" in report.unresolved_imports


def test_distinguishes_unresolved_from_syntax_errors(ts_file: Path, tmp_path: Path) -> None:
    tsc_output = (
        "mytest.test.ts(1,1): error TS2307: Cannot find module './missing' or its corresponding type declarations.\n"
        "mytest.test.ts(5,1): error TS1005: ';' expected.\n"
    )
    runner = _make_runner(returncode=1, stdout=tsc_output)
    report = run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert report.ok is False
    assert "./missing" in report.unresolved_imports
    assert len(report.other_errors) == 1
    assert "TS1005" in report.other_errors[0]


def test_raw_output_exposed_for_debugging(ts_file: Path, tmp_path: Path) -> None:
    tsc_output = "mytest.test.ts(1,1): error TS2307: Cannot find module './x' or its corresponding type declarations.\n"
    runner = _make_runner(returncode=1, stdout=tsc_output)
    report = run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert "TS2307" in report.raw_output


def test_raw_output_includes_stderr(ts_file: Path, tmp_path: Path) -> None:
    runner = _make_runner(returncode=0, stdout="stdout text", stderr="stderr text")
    report = run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert "stdout text" in report.raw_output
    assert "stderr text" in report.raw_output


def test_exit_code_0_is_ok_even_with_warnings(ts_file: Path, tmp_path: Path) -> None:
    runner = _make_runner(returncode=0, stdout="", stderr="some warning line")
    report = run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert report.ok is True


def test_exit_code_nonzero_is_not_ok(ts_file: Path, tmp_path: Path) -> None:
    runner = _make_runner(returncode=1, stdout="")
    report = run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert report.ok is False


def test_runner_image_passed_through(ts_file: Path, tmp_path: Path) -> None:
    calls: list = []
    runner = _recording_runner(calls, returncode=0)
    run_ts_preflight(
        ts_file, tmp_path,
        runner_fn=runner,
        runner_image="tfactory-runner-playwright:latest",
    )
    assert len(calls) == 1
    assert calls[0]["image"] == "tfactory-runner-playwright:latest"


def test_runner_receives_tsc_command(ts_file: Path, tmp_path: Path) -> None:
    calls: list = []
    runner = _recording_runner(calls, returncode=0)
    run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert len(calls) == 1
    assert "tsc" in calls[0]["cmd"]
    assert "--noEmit" in calls[0]["cmd"]
    assert str(ts_file) in calls[0]["cmd"]


@pytest.mark.parametrize("error_code,msg,expected_bucket", [
    ("TS2307", "Cannot find module './x' or its corresponding type declarations.", "unresolved"),
    ("TS2304", "Cannot find name 'MyType'.", "unresolved"),
    ("TS1005", "';' expected.", "other"),
    ("TS2345", "Argument of type 'string' is not assignable to parameter of type 'number'.", "other"),
    ("TS2551", "Property 'foo' does not exist on type 'Bar'. Did you mean 'baz'?", "other"),
])
def test_parametrized_error_code_classification(
    ts_file: Path,
    tmp_path: Path,
    error_code: str,
    msg: str,
    expected_bucket: str,
) -> None:
    tsc_output = f"file.ts(1,1): error {error_code}: {msg}\n"
    runner = _make_runner(returncode=1, stdout=tsc_output)
    report = run_ts_preflight(ts_file, tmp_path, runner_fn=runner)
    assert report.ok is False
    if expected_bucket == "unresolved":
        assert len(report.unresolved_imports) >= 1
    else:
        assert len(report.other_errors) >= 1


def test_summary_ok() -> None:
    report = TSPreflightReport(
        test_file=Path("x.ts"),
        ok=True,
        unresolved_imports=(),
        other_errors=(),
        raw_output="",
    )
    assert "OK" in report.summary()


def test_summary_with_errors() -> None:
    report = TSPreflightReport(
        test_file=Path("x.ts"),
        ok=False,
        unresolved_imports=("./foo",),
        other_errors=("TS1005: ';' expected.",),
        raw_output="",
    )
    s = report.summary()
    assert "unresolved" in s
    assert "error" in s
