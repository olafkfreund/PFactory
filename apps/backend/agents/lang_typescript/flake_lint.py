"""TypeScript flake-risk lint — Task 9 (#25) commit 2.

LLM-generated TypeScript tests carry a specific set of anti-patterns that
make them flaky or non-deterministic.  This module runs ESLint with a
PFactory-tuned configuration (``eslint-pfactory.config.json``, commit 4)
inside the runner Docker image and classifies the findings by severity.

The severity model mirrors the Python sibling (``flake_risk_lint.py``):

  high   — REJECT.  Gen-Functional treats the file as rejected and
           triggers a Planner replan.
  medium — FLAG.    The Evaluator decides whether to accept or downgrade
           the verdict based on the rest of the signal.

High-severity rules (auto-reject):
  playwright/no-wait-for-timeout    ``await page.waitForTimeout(N)``
  pfactory/no-hardcoded-sleep       bare ``setTimeout(fn, N)`` with a
                                    numeric literal in test bodies
  pfactory/no-math-random-no-seed   ``Math.random()`` without a
                                    deterministic seed

Medium-severity rules (flag only):
  no-console                        ``console.log`` / ``console.error``
                                    in tests
  playwright/expect-expect          assertions present in test
  @typescript-eslint/no-non-null-assertion  ``!`` non-null assertions

ESLint is run with ``--format json`` so the output is machine-parseable.
Exit code 1 means findings were emitted; exit code 0 means clean; exit
code 2 means ESLint itself crashed (mapped to an error finding).

Public API::

    report = run_ts_flake_lint(test_file, project_dir)
    if report.has_high:
        # reject — trigger Planner replan
    elif report.has_medium:
        # flag — let Evaluator decide
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

# ─── Severity mapping ────────────────────────────────────────────────────────

# ESLint rule IDs → PFactory severity tier.
# Rules NOT in this table fall through to a "medium" default if their
# ESLint severity is 2 (error), or are ignored if severity is 1 (warn).
_RULE_SEVERITY: dict[str, Literal["high", "medium"]] = {
    # High (auto-reject)
    "playwright/no-wait-for-timeout": "high",
    "pfactory/no-hardcoded-sleep": "high",
    "no-hardcoded-sleep": "high",  # alternative rule ID
    "pfactory/no-math-random-no-seed": "high",
    "no-math-random-no-seed": "high",  # alternative rule ID
    # Medium (flag)
    "no-console": "medium",
    "playwright/expect-expect": "medium",
    "@typescript-eslint/no-non-null-assertion": "medium",
    "no-non-null-assertion": "medium",  # sometimes reported without scope
}

# ─── Result types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TSFlakeFinding:
    """A single ESLint finding classified by PFactory severity.

    Attributes:
        rule: ESLint rule ID (e.g. ``"playwright/no-wait-for-timeout"``).
        severity: ``"high"`` (auto-reject) or ``"medium"`` (flag).
        line: Source line number where the finding occurs.
        message: ESLint's human-readable message for the finding.
    """

    rule: str
    severity: Literal["high", "medium"]
    line: int
    message: str


@dataclass(frozen=True)
class TSFlakeReport:
    """Aggregate lint outcome for one TypeScript test file.

    Attributes:
        test_file: The file that was linted.
        findings: All :class:`TSFlakeFinding` instances (empty if clean).
        has_high: True if any high-severity finding was recorded.
        has_medium: True if any medium-severity finding was recorded.
    """

    test_file: Path
    findings: tuple[TSFlakeFinding, ...]
    has_high: bool
    has_medium: bool

    def summary(self) -> str:
        """Human-readable one-line summary."""
        if not self.findings:
            return "OK (no flake-risk patterns detected)"
        parts = []
        high = [f for f in self.findings if f.severity == "high"]
        medium = [f for f in self.findings if f.severity == "medium"]
        if high:
            parts.append(f"{len(high)} reject")
        if medium:
            parts.append(f"{len(medium)} flag")
        return ", ".join(parts)


# ─── ESLint JSON output parser ───────────────────────────────────────────────


def _severity_for_rule(
    rule_id: str, eslint_severity: int
) -> Literal["high", "medium"] | None:
    """Map an ESLint rule ID + numeric severity to a PFactory severity tier.

    Returns:
        ``"high"`` or ``"medium"`` if the finding is actionable;
        ``None`` if it should be silently ignored.
    """
    # Explicit mapping wins.
    if rule_id in _RULE_SEVERITY:
        return _RULE_SEVERITY[rule_id]

    # Fall through: ESLint severity 2 (error) → medium, 1 (warn) → ignore.
    if eslint_severity == 2:
        return "medium"
    return None


def _parse_eslint_json(raw: str, test_file: Path) -> tuple[TSFlakeFinding, ...]:
    """Parse ESLint's ``--format json`` output into :class:`TSFlakeFinding` instances.

    Args:
        raw: The stdout from the ESLint invocation (must be valid JSON).
        test_file: Used for error context messages only.

    Returns:
        Tuple of findings.  Empty if no findings or on parse error.
    """
    if not raw.strip():
        return ()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ESLint JSON parse error for %s; raw: %.200s", test_file, raw)
        return ()

    findings: list[TSFlakeFinding] = []

    # ESLint JSON format: list of file-level result objects.
    # Each has "messages": [{"ruleId": ..., "severity": ..., "line": ..., "message": ...}]
    for file_result in data:
        messages = file_result.get("messages", [])
        for msg in messages:
            rule_id = msg.get("ruleId") or ""
            eslint_sev = msg.get("severity", 1)
            line = msg.get("line", 0)
            message = msg.get("message", "")

            pfactory_sev = _severity_for_rule(rule_id, eslint_sev)
            if pfactory_sev is None:
                continue

            findings.append(
                TSFlakeFinding(
                    rule=rule_id,
                    severity=pfactory_sev,
                    line=line,
                    message=message,
                )
            )

    return tuple(findings)


# ─── Default config path ─────────────────────────────────────────────────────

_BUNDLED_CONFIG = Path(__file__).parent / "eslint-pfactory.config.json"


# ─── Public entry point ──────────────────────────────────────────────────────


def run_ts_flake_lint(
    test_file: Path,
    project_dir: Path,
    *,
    runner_fn: Callable[..., Any] | None = None,
    runner_image: str = "pfactory-runner-jest:latest",
    config_path: Path | None = None,
    timeout: int = 60,
) -> TSFlakeReport:
    """Run ESLint with PFactory's flake-detecting config on ``test_file``.

    Args:
        test_file: Absolute path to the TypeScript test file to lint.
        project_dir: Project root (mounted into the container so that
            node_modules resolve correctly).
        runner_fn: Injection point for tests.  Must accept
            ``(cmd: list[str], cwd: str, *, image: str, timeout: int)``
            and return an object with ``.returncode``, ``.stdout``,
            ``.stderr``.  Defaults to a real DockerRunner invocation.
        runner_image: Docker image that contains ESLint.  Defaults to
            ``pfactory-runner-jest:latest`` (Task 7 build).
        config_path: Path to the ESLint flat-config JSON.  Defaults to
            the bundled ``eslint-pfactory.config.json`` in this package.
        timeout: Container run timeout in seconds.

    Returns:
        :class:`TSFlakeReport`
    """
    effective_config = config_path or _BUNDLED_CONFIG

    # ESLint flat-config v9: --config <path> --format json --no-error-on-unmatched-pattern
    cmd = [
        "npx",
        "eslint",
        "--config",
        str(effective_config),
        "--format",
        "json",
        "--no-error-on-unmatched-pattern",
        str(test_file),
    ]
    cwd = str(project_dir)

    if runner_fn is None:
        from tools.runners.docker_runner import DockerRunner  # noqa: PLC0415

        runner = DockerRunner(image=runner_image, timeout=timeout)
        result = runner.run(cmd, cwd=cwd)
    else:
        result = runner_fn(cmd, cwd, image=runner_image, timeout=timeout)

    raw_output = result.stdout or ""

    # ESLint exit codes:
    #   0 — no findings
    #   1 — findings present (also when linting fails due to config issues)
    #   2 — eslint itself crashed
    if result.returncode == 2:
        # Treat ESLint crash as a finding to surface the problem.
        crash_finding = TSFlakeFinding(
            rule="eslint/crash",
            severity="medium",
            line=0,
            message=f"ESLint exited with code 2: {(result.stderr or '')[:200]}",
        )
        return TSFlakeReport(
            test_file=test_file,
            findings=(crash_finding,),
            has_high=False,
            has_medium=True,
        )

    findings = _parse_eslint_json(raw_output, test_file)
    has_high = any(f.severity == "high" for f in findings)
    has_medium = any(f.severity == "medium" for f in findings)

    return TSFlakeReport(
        test_file=test_file,
        findings=findings,
        has_high=has_high,
        has_medium=has_medium,
    )
