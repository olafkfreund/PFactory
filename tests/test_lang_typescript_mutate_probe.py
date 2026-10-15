"""Tests for the TypeScript mutation probe — Task 9 (#25) commit 5.

Covers:
  - KILLED: mutated assertion fails the test → KILLED
  - SURVIVED: mutated assertion passes → SURVIVED (test is too weak)
  - NO_MUTANT: test file has no assertions → NO_MUTANT
  - SYNTAX_ERROR: file doesn't exist or can't be read → SYNTAX_ERROR
  - TIMEOUT: Stryker timeout status in JSON → TIMEOUT verdict
  - Detects and mutates expect(x).toBe(y) — picks numeric literal y
  - Detects and mutates expect(x).toBe(true/false)
  - Detects expect(x).toHaveLength(N) — mutates N
  - Preserves original test file (mutant written to temp dir)
  - raw_stryker_json is parsed and exposed on the report
  - mutated_assertion field gives human-readable description
  - Handles Stryker exit code 0 and non-zero gracefully
  - runner_fn mock simulates the full Stryker JSON report shape
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from agents.lang_typescript.mutate_probe import (
    TSMutateReport,
    TSMutationVerdict,
    _mutate_source,
    _parse_stryker_report,
    run_ts_mutate_probe,
)

# ─── Fake runner ──────────────────────────────────────────────────────────────


@dataclass
class _FakeRunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _stryker_json(mutants: list[dict]) -> str:
    return json.dumps({"schemaVersion": "2.0", "mutants": mutants})


def _make_runner(
    returncode: int = 0,
    mutants: list[dict] | None = None,
) -> object:
    mutants = mutants or []
    stdout = _stryker_json(mutants)
    def _runner(cmd, cwd, *, image, timeout):
        return _FakeRunResult(returncode=returncode, stdout=stdout)
    return _runner


def _recording_runner(calls: list, returncode: int = 0, mutants: list | None = None):
    mutants = mutants or []
    stdout = _stryker_json(mutants)
    def _runner(cmd, cwd, *, image, timeout):
        calls.append({"cmd": cmd, "cwd": cwd, "image": image, "timeout": timeout})
        return _FakeRunResult(returncode=returncode, stdout=stdout)
    return _runner


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


def _write_ts(tmp_path: Path, source: str, name: str = "mytest.test.ts") -> Path:
    f = tmp_path / name
    f.write_text(source)
    return f


# ─── _mutate_source unit tests ────────────────────────────────────────────────


def test_mutate_tobe_numeric_literal() -> None:
    source = "test('x', () => { expect(result).toBe(5); });\n"
    mutated, desc = _mutate_source(source)
    assert mutated is not None
    assert desc is not None
    assert "toBe(6)" in mutated
    assert "5" in desc or "6" in desc


def test_mutate_tobe_true() -> None:
    source = "test('x', () => { expect(flag).toBe(true); });\n"
    mutated, desc = _mutate_source(source)
    assert mutated is not None
    assert "false" in mutated
    assert "true" in desc or "false" in desc


def test_mutate_tobe_false() -> None:
    source = "test('x', () => { expect(flag).toBe(false); });\n"
    mutated, desc = _mutate_source(source)
    assert mutated is not None
    assert "true" in mutated


def test_mutate_to_have_length() -> None:
    source = "test('x', () => { expect(arr).toHaveLength(3); });\n"
    mutated, desc = _mutate_source(source)
    assert mutated is not None
    assert "4" in mutated or "toHaveLength(4)" in mutated


def test_mutate_to_equal_first_number() -> None:
    source = "test('x', () => { expect(obj).toEqual({ a: 1 }); });\n"
    mutated, desc = _mutate_source(source)
    # Should mutate the first numeric value (1 → 2)
    assert mutated is not None
    assert "2" in mutated


def test_mutate_preserves_original_string() -> None:
    """The function returns a new string; original is unchanged."""
    source = "test('x', () => { expect(x).toBe(5); });\n"
    original = source
    _mutate_source(source)
    assert source == original


def test_no_assertion_returns_none() -> None:
    source = "const x = 5;\nconst y = x + 1;\n"
    mutated, desc = _mutate_source(source)
    assert mutated is None
    assert desc is None


def test_empty_source_returns_none() -> None:
    mutated, desc = _mutate_source("")
    assert mutated is None
    assert desc is None


def test_mutate_describes_location() -> None:
    source = "test('x', () => { expect(val).toBe(10); });\n"
    _, desc = _mutate_source(source)
    assert desc is not None
    assert "line" in desc


# ─── _parse_stryker_report unit tests ─────────────────────────────────────────


def test_parse_killed_verdict() -> None:
    raw = _stryker_json([{"id": "1", "status": "Killed"}])
    verdict, data = _parse_stryker_report(raw)
    assert verdict == TSMutationVerdict.KILLED
    assert data is not None


def test_parse_survived_verdict() -> None:
    raw = _stryker_json([{"id": "1", "status": "Survived"}])
    verdict, _ = _parse_stryker_report(raw)
    assert verdict == TSMutationVerdict.SURVIVED


def test_parse_timeout_verdict() -> None:
    raw = _stryker_json([{"id": "1", "status": "Timeout"}])
    verdict, _ = _parse_stryker_report(raw)
    assert verdict == TSMutationVerdict.TIMEOUT


def test_parse_compile_error_verdict() -> None:
    raw = _stryker_json([{"id": "1", "status": "CompileError"}])
    verdict, _ = _parse_stryker_report(raw)
    assert verdict == TSMutationVerdict.SYNTAX_ERROR


def test_parse_runtime_error_verdict() -> None:
    raw = _stryker_json([{"id": "1", "status": "RuntimeError"}])
    verdict, _ = _parse_stryker_report(raw)
    assert verdict == TSMutationVerdict.SYNTAX_ERROR


def test_parse_no_mutants_returns_no_mutant() -> None:
    raw = _stryker_json([])
    verdict, _ = _parse_stryker_report(raw)
    assert verdict == TSMutationVerdict.NO_MUTANT


def test_parse_empty_string_returns_no_mutant() -> None:
    verdict, data = _parse_stryker_report("")
    assert verdict == TSMutationVerdict.NO_MUTANT
    assert data is None


def test_parse_json_with_prefix_log_lines() -> None:
    """Stryker sometimes outputs log lines before the JSON."""
    prefix = "[Stryker] INFO log line\n[Stryker] DEBUG whatever\n"
    json_part = _stryker_json([{"id": "1", "status": "Killed"}])
    verdict, _ = _parse_stryker_report(prefix + json_part)
    assert verdict == TSMutationVerdict.KILLED


def test_parse_exposes_full_stryker_dict() -> None:
    raw = _stryker_json([{"id": "42", "status": "Killed", "location": {"start": {"line": 3}}}])
    _, data = _parse_stryker_report(raw)
    assert data is not None
    assert "mutants" in data
    assert data["mutants"][0]["id"] == "42"


# ─── run_ts_mutate_probe integration tests ───────────────────────────────────


def test_killed_when_mutant_fails(tmp_path: Path, project_dir: Path) -> None:
    test_file = _write_ts(tmp_path, "test('x', () => { expect(val).toBe(5); });\n")
    runner = _make_runner(returncode=0, mutants=[{"id": "1", "status": "Killed"}])
    report = run_ts_mutate_probe(test_file, project_dir, runner_fn=runner)
    assert isinstance(report, TSMutateReport)
    assert report.verdict == TSMutationVerdict.KILLED
    assert report.mutated_assertion is not None


def test_survived_when_mutant_passes(tmp_path: Path, project_dir: Path) -> None:
    test_file = _write_ts(tmp_path, "test('x', () => { expect(val).toBe(true); });\n")
    runner = _make_runner(returncode=0, mutants=[{"id": "1", "status": "Survived"}])
    report = run_ts_mutate_probe(test_file, project_dir, runner_fn=runner)
    assert report.verdict == TSMutationVerdict.SURVIVED


def test_no_mutant_when_no_assertions(tmp_path: Path, project_dir: Path) -> None:
    test_file = _write_ts(tmp_path, "const x = 5;\n")
    def _should_not_run(*a, **kw):
        pytest.fail("runner should not be called when no mutation found")
    report = run_ts_mutate_probe(test_file, project_dir, runner_fn=_should_not_run)
    assert report.verdict == TSMutationVerdict.NO_MUTANT
    assert report.mutated_assertion is None


def test_syntax_error_when_file_not_found(tmp_path: Path, project_dir: Path) -> None:
    missing = tmp_path / "nonexistent.test.ts"
    report = run_ts_mutate_probe(missing, project_dir, runner_fn=_make_runner())
    assert report.verdict == TSMutationVerdict.SYNTAX_ERROR


def test_timeout_verdict_from_stryker(tmp_path: Path, project_dir: Path) -> None:
    test_file = _write_ts(tmp_path, "test('x', () => { expect(n).toBe(1); });\n")
    runner = _make_runner(returncode=0, mutants=[{"id": "1", "status": "Timeout"}])
    report = run_ts_mutate_probe(test_file, project_dir, runner_fn=runner)
    assert report.verdict == TSMutationVerdict.TIMEOUT


def test_preserves_original_test_file(tmp_path: Path, project_dir: Path) -> None:
    """The probe must NOT write over the original test file."""
    source = "test('x', () => { expect(val).toBe(5); });\n"
    test_file = _write_ts(tmp_path, source)
    runner = _make_runner(returncode=0, mutants=[{"id": "1", "status": "Killed"}])
    run_ts_mutate_probe(test_file, project_dir, runner_fn=runner)
    # Original must be intact.
    assert test_file.read_text() == source


def test_raw_stryker_json_exposed(tmp_path: Path, project_dir: Path) -> None:
    test_file = _write_ts(tmp_path, "test('x', () => { expect(n).toBe(3); });\n")
    mutants = [{"id": "99", "status": "Killed", "extra": "data"}]
    runner = _make_runner(returncode=0, mutants=mutants)
    report = run_ts_mutate_probe(test_file, project_dir, runner_fn=runner)
    assert report.raw_stryker_json is not None
    assert report.raw_stryker_json["mutants"][0]["id"] == "99"


def test_mutated_assertion_field_human_readable(tmp_path: Path, project_dir: Path) -> None:
    test_file = _write_ts(tmp_path, "test('x', () => { expect(val).toBe(7); });\n")
    runner = _make_runner(returncode=0, mutants=[{"id": "1", "status": "Killed"}])
    report = run_ts_mutate_probe(test_file, project_dir, runner_fn=runner)
    assert report.mutated_assertion is not None
    # Should contain original and mutated value descriptions.
    assert "7" in report.mutated_assertion or "8" in report.mutated_assertion


def test_runner_fn_receives_stryker_command(tmp_path: Path, project_dir: Path) -> None:
    test_file = _write_ts(tmp_path, "test('x', () => { expect(n).toBe(1); });\n")
    calls: list = []
    runner = _recording_runner(calls, returncode=0, mutants=[{"id": "1", "status": "Killed"}])
    run_ts_mutate_probe(test_file, project_dir, runner_fn=runner)
    assert len(calls) == 1
    assert any("stryker" in str(c).lower() for c in calls[0]["cmd"])


def test_runner_image_passed_through(tmp_path: Path, project_dir: Path) -> None:
    test_file = _write_ts(tmp_path, "test('x', () => { expect(n).toBe(2); });\n")
    calls: list = []
    runner = _recording_runner(calls, returncode=0, mutants=[{"id": "1", "status": "Killed"}])
    run_ts_mutate_probe(
        test_file, project_dir,
        runner_fn=runner,
        runner_image="tfactory-runner-playwright:latest",
    )
    assert calls[0]["image"] == "tfactory-runner-playwright:latest"


def test_stryker_exit_nonzero_still_parses_report(tmp_path: Path, project_dir: Path) -> None:
    """Stryker may exit non-zero even on a successful run with some unresolved mutants."""
    test_file = _write_ts(tmp_path, "test('x', () => { expect(n).toHaveLength(3); });\n")
    # Stryker exits 1 but still emits a JSON report.
    runner = _make_runner(returncode=1, mutants=[{"id": "1", "status": "Survived"}])
    report = run_ts_mutate_probe(test_file, project_dir, runner_fn=runner)
    # We still parse the Stryker JSON and extract the verdict.
    assert report.verdict == TSMutationVerdict.SURVIVED
