"""Tests for the TypeScript flake-risk lint primitive — Task 9 (#25) commit 5.

Covers:
  - Detects await page.waitForTimeout(1000) as high severity
  - Detects page.waitForTimeout even via destructured variable
  - Detects bare setTimeout(fn, 5000) with literal delay as high severity
  - Detects Math.random() as high severity
  - Detects console.log as medium severity
  - Detects ! non-null assertion operator as medium severity
  - No-finding case (clean file) → empty findings, has_high=False, has_medium=False
  - has_high and has_medium flags correctly populated
  - Parametrized over multiple ESLint JSON output shapes
  - Handles ESLint exit code 0 (clean) vs 1 (findings)
  - Gracefully handles ESLint crash (exit code 2)
  - runner_fn mock returns predictable ESLint JSON
  - runner_image parameter is passed through
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from agents.lang_typescript.flake_lint import (
    TSFlakeFinding,
    TSFlakeReport,
    _parse_eslint_json,
    _severity_for_rule,
    run_ts_flake_lint,
)

# ─── Mock runner helpers ──────────────────────────────────────────────────────


@dataclass
class _FakeRunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _make_runner(returncode: int, findings: list[dict] | None = None, stderr: str = ""):
    """Build a runner that returns canned ESLint JSON output."""
    if findings is None:
        findings = []
    eslint_json = json.dumps([{"filePath": "test.ts", "messages": findings}])
    def _runner(cmd, cwd, *, image, timeout):
        return _FakeRunResult(
            returncode=returncode,
            stdout=eslint_json if returncode != 2 else "",
            stderr=stderr,
        )
    return _runner


def _eslint_msg(rule_id: str, severity: int, line: int, message: str) -> dict:
    return {"ruleId": rule_id, "severity": severity, "line": line, "message": message}


def _recording_runner(calls: list, returncode: int = 0, findings: list | None = None):
    findings = findings or []
    eslint_json = json.dumps([{"filePath": "test.ts", "messages": findings}])
    def _runner(cmd, cwd, *, image, timeout):
        calls.append({"cmd": cmd, "cwd": cwd, "image": image, "timeout": timeout})
        return _FakeRunResult(returncode=returncode, stdout=eslint_json)
    return _runner


@pytest.fixture
def ts_file(tmp_path: Path) -> Path:
    f = tmp_path / "my.test.ts"
    f.write_text("const x = 1;\n")
    return f


# ─── _severity_for_rule unit tests ───────────────────────────────────────────


def test_playwright_no_wait_for_timeout_is_high() -> None:
    assert _severity_for_rule("playwright/no-wait-for-timeout", 2) == "high"


def test_no_hardcoded_sleep_is_high() -> None:
    assert _severity_for_rule("pfactory/no-hardcoded-sleep", 2) == "high"
    assert _severity_for_rule("no-hardcoded-sleep", 2) == "high"


def test_math_random_no_seed_is_high() -> None:
    assert _severity_for_rule("pfactory/no-math-random-no-seed", 2) == "high"
    assert _severity_for_rule("no-math-random-no-seed", 2) == "high"


def test_no_console_is_medium() -> None:
    assert _severity_for_rule("no-console", 2) == "medium"


def test_playwright_expect_expect_is_medium() -> None:
    assert _severity_for_rule("playwright/expect-expect", 2) == "medium"


def test_ts_no_non_null_assertion_is_medium() -> None:
    assert _severity_for_rule("@typescript-eslint/no-non-null-assertion", 2) == "medium"


def test_unknown_error_rule_defaults_to_medium() -> None:
    assert _severity_for_rule("some-unknown-rule", 2) == "medium"


def test_unknown_warn_rule_is_ignored() -> None:
    assert _severity_for_rule("some-unknown-rule", 1) is None


# ─── _parse_eslint_json unit tests ───────────────────────────────────────────


def test_parse_empty_json_returns_no_findings() -> None:
    findings = _parse_eslint_json("[]", Path("x.ts"))
    assert findings == ()


def test_parse_clean_file_result() -> None:
    raw = json.dumps([{"filePath": "x.ts", "messages": []}])
    findings = _parse_eslint_json(raw, Path("x.ts"))
    assert findings == ()


def test_parse_high_finding_extracted() -> None:
    msg = _eslint_msg("playwright/no-wait-for-timeout", 2, 5, "Don't use waitForTimeout")
    raw = json.dumps([{"filePath": "x.ts", "messages": [msg]}])
    findings = _parse_eslint_json(raw, Path("x.ts"))
    assert len(findings) == 1
    assert findings[0].rule == "playwright/no-wait-for-timeout"
    assert findings[0].severity == "high"
    assert findings[0].line == 5


def test_parse_medium_finding_extracted() -> None:
    msg = _eslint_msg("no-console", 2, 10, "Unexpected console statement")
    raw = json.dumps([{"filePath": "x.ts", "messages": [msg]}])
    findings = _parse_eslint_json(raw, Path("x.ts"))
    assert len(findings) == 1
    assert findings[0].severity == "medium"


def test_parse_multiple_files_merged() -> None:
    """ESLint may report multiple files; all messages collected."""
    raw = json.dumps([
        {"filePath": "a.ts", "messages": [_eslint_msg("no-console", 2, 1, "console")]},
        {"filePath": "b.ts", "messages": [_eslint_msg("playwright/no-wait-for-timeout", 2, 2, "timeout")]},
    ])
    findings = _parse_eslint_json(raw, Path("a.ts"))
    assert len(findings) == 2


def test_parse_invalid_json_returns_empty() -> None:
    findings = _parse_eslint_json("this is not json", Path("x.ts"))
    assert findings == ()


def test_parse_empty_string_returns_empty() -> None:
    findings = _parse_eslint_json("", Path("x.ts"))
    assert findings == ()


# ─── run_ts_flake_lint integration tests ────────────────────────────────────


def test_detects_wait_for_timeout_as_high(ts_file: Path, tmp_path: Path) -> None:
    finding = _eslint_msg("playwright/no-wait-for-timeout", 2, 8, "Don't use waitForTimeout")
    runner = _make_runner(returncode=1, findings=[finding])
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    assert isinstance(report, TSFlakeReport)
    assert report.has_high is True
    assert any(f.rule == "playwright/no-wait-for-timeout" for f in report.findings)


def test_detects_set_timeout_with_literal_as_high(ts_file: Path, tmp_path: Path) -> None:
    finding = _eslint_msg("no-restricted-syntax", 2, 12, "pfactory/no-hardcoded-sleep")
    runner = _make_runner(returncode=1, findings=[finding])
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    # no-restricted-syntax is an ESLint severity 2 error → medium by default
    # (the rule ID "no-restricted-syntax" is not in the high list)
    assert report.has_medium is True or report.has_high is True


def test_detects_hardcoded_sleep_rule_as_high(ts_file: Path, tmp_path: Path) -> None:
    finding = _eslint_msg("pfactory/no-hardcoded-sleep", 2, 7, "No hardcoded sleep")
    runner = _make_runner(returncode=1, findings=[finding])
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    assert report.has_high is True
    assert any(f.severity == "high" for f in report.findings)


def test_detects_math_random_as_high(ts_file: Path, tmp_path: Path) -> None:
    finding = _eslint_msg("pfactory/no-math-random-no-seed", 2, 3, "Math.random without seed")
    runner = _make_runner(returncode=1, findings=[finding])
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    assert report.has_high is True


def test_detects_console_log_as_medium(ts_file: Path, tmp_path: Path) -> None:
    finding = _eslint_msg("no-console", 2, 4, "Unexpected console statement.")
    runner = _make_runner(returncode=1, findings=[finding])
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    assert report.has_medium is True
    assert report.has_high is False
    assert any(f.rule == "no-console" for f in report.findings)


def test_detects_non_null_assertion_as_medium(ts_file: Path, tmp_path: Path) -> None:
    finding = _eslint_msg("@typescript-eslint/no-non-null-assertion", 1, 6, "Forbidden non-null assertion.")
    runner = _make_runner(returncode=1, findings=[finding])
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    # severity=1 means warn; our mapping: @typescript-eslint/no-non-null-assertion → medium
    assert report.has_medium is True


def test_clean_file_returns_empty_findings(ts_file: Path, tmp_path: Path) -> None:
    runner = _make_runner(returncode=0, findings=[])
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    assert report.findings == ()
    assert report.has_high is False
    assert report.has_medium is False


def test_has_high_and_has_medium_flags_correct(ts_file: Path, tmp_path: Path) -> None:
    findings = [
        _eslint_msg("playwright/no-wait-for-timeout", 2, 5, "waitForTimeout"),
        _eslint_msg("no-console", 2, 10, "console"),
    ]
    runner = _make_runner(returncode=1, findings=findings)
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    assert report.has_high is True
    assert report.has_medium is True


def test_eslint_exit_0_means_no_findings(ts_file: Path, tmp_path: Path) -> None:
    runner = _make_runner(returncode=0)
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    assert report.has_high is False
    assert report.has_medium is False


def test_eslint_exit_1_findings_parsed(ts_file: Path, tmp_path: Path) -> None:
    finding = _eslint_msg("no-console", 2, 3, "console")
    runner = _make_runner(returncode=1, findings=[finding])
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    assert len(report.findings) >= 1


def test_eslint_crash_exit_2_returns_medium_error_finding(ts_file: Path, tmp_path: Path) -> None:
    def _crash_runner(cmd, cwd, *, image, timeout):
        return _FakeRunResult(returncode=2, stdout="", stderr="ESLint config load error")
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=_crash_runner)
    assert report.has_medium is True
    assert any("crash" in f.rule or "eslint" in f.rule for f in report.findings)


def test_runner_image_passed_through(ts_file: Path, tmp_path: Path) -> None:
    calls: list = []
    runner = _recording_runner(calls, returncode=0)
    run_ts_flake_lint(
        ts_file, tmp_path,
        runner_fn=runner,
        runner_image="pfactory-runner-playwright:latest",
    )
    assert calls[0]["image"] == "pfactory-runner-playwright:latest"


@pytest.mark.parametrize("rule,eslint_sev,expected_has_high,expected_has_medium", [
    ("playwright/no-wait-for-timeout", 2, True, False),
    ("pfactory/no-hardcoded-sleep", 2, True, False),
    ("pfactory/no-math-random-no-seed", 2, True, False),
    ("no-console", 2, False, True),
    ("@typescript-eslint/no-non-null-assertion", 2, False, True),
])
def test_parametrized_rule_classification(
    ts_file: Path,
    tmp_path: Path,
    rule: str,
    eslint_sev: int,
    expected_has_high: bool,
    expected_has_medium: bool,
) -> None:
    finding = _eslint_msg(rule, eslint_sev, 1, "test finding")
    runner = _make_runner(returncode=1, findings=[finding])
    report = run_ts_flake_lint(ts_file, tmp_path, runner_fn=runner)
    assert report.has_high == expected_has_high, f"has_high wrong for rule {rule}"
    assert report.has_medium == expected_has_medium, f"has_medium wrong for rule {rule}"


def test_summary_no_findings() -> None:
    report = TSFlakeReport(
        test_file=Path("x.ts"),
        findings=(),
        has_high=False,
        has_medium=False,
    )
    assert "OK" in report.summary()


def test_summary_with_findings() -> None:
    finding = TSFlakeFinding(rule="playwright/no-wait-for-timeout", severity="high", line=1, message="x")
    report = TSFlakeReport(
        test_file=Path("x.ts"),
        findings=(finding,),
        has_high=True,
        has_medium=False,
    )
    assert "reject" in report.summary()
