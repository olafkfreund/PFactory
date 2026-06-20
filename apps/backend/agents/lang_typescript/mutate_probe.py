"""TypeScript mutation probe — Task 9 (#25) commit 3.

The strongest of the Evaluator's signals for TypeScript: uses Stryker to
apply ONE mutation to an assertion in the generated test file, then re-run.
If the mutated test *still passes*, the assertion doesn't actually constrain
the behavior under test — the test "survived" the mutation.

The Python sibling (``mutate_probe.py``) uses AST rewriting directly;
TypeScript needs a real compiler so we delegate to Stryker's mutation
engine.  This module:

  1. Picks the first ``expect(...)`` assertion in the test file via
     a lightweight regex (e.g. ``expect(x).toBe(5)``).
  2. Mutates the expected value (5 → 6 for numeric literals,
     true → false for booleans, etc.) in a temporary copy.
  3. Runs Stryker in ``dry-run`` mode pointing at only the mutated file.
  4. Parses Stryker's JSON report to classify the first mutant as
     KILLED / SURVIVED / etc.

Stryker exits 0 if its run completed successfully (regardless of
KILLED/SURVIVED outcomes — those are in the JSON report).  The
classification is done by reading the report.

Public API::

    report = run_ts_mutate_probe(test_file, project_dir)
    if report.verdict == TSMutationVerdict.SURVIVED:
        # assertion is tautological — reject
    elif report.verdict == TSMutationVerdict.KILLED:
        # assertion is meaningful — accept

Implementation note on runner_fn injection:

    The default ``runner_fn=None`` path constructs a DockerRunner and runs
    Stryker inside the container.  Tests inject a mock callable with the
    signature:

        runner_fn(cmd: list[str], cwd: str) -> _RunResultLike

    The mock returns a fake Stryker JSON report via ``result.stdout``.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── Verdict enum ─────────────────────────────────────────────────────────────


class TSMutationVerdict(str, Enum):
    """Outcome of a Stryker mutation probe on one TypeScript test."""

    KILLED = "killed"  # mutant failed — assertion is meaningful
    SURVIVED = "survived"  # mutant passed — assertion is tautological / weak
    NO_MUTANT = "no_mutant"  # no mutable assertion found in the test file
    SYNTAX_ERROR = "syntax_error"  # mutated file has a parse/type error
    TIMEOUT = "timeout"  # Stryker timed out


# ─── Result type ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TSMutateReport:
    """Outcome of one Stryker mutation probe.

    Attributes:
        test_file: The original (unmutated) TypeScript test file.
        verdict: Classification of the mutation result.
        mutated_assertion: Human-readable description of the mutation applied
            (e.g. ``"expect(x).toBe(5) → expect(x).toBe(6)"``).
            ``None`` if no mutation was applicable.
        raw_stryker_json: The parsed Stryker JSON report dict, or ``None``
            if parsing failed or no mutation was applied.
    """

    test_file: Path
    verdict: TSMutationVerdict
    mutated_assertion: str | None
    raw_stryker_json: dict | None


# ─── Assertion mutation helpers ───────────────────────────────────────────────

# Regex patterns for common Jest/Playwright assertion forms.
# We target the *value* argument (second arg for toBe, toEqual, etc.).
_ASSERTION_PATTERNS = [
    # expect(x).toBe(5) → literal numeric
    re.compile(r"(\.toBe\()(\d+)(\))"),
    # expect(x).toBe(true) / .toBe(false)
    re.compile(r"(\.toBe\()(true|false)(\))"),
    # expect(x).toEqual({a: 1}) → first numeric value in object literal
    re.compile(r"(:\s*)(\d+)(\s*[,}])"),
    # expect(x).toHaveLength(N)
    re.compile(r"(\.toHaveLength\()(\d+)(\))"),
    # expect(x).toBeGreaterThan(N)
    re.compile(r"(\.toBeGreaterThan\()(\d+)(\))"),
    # expect(x).toBeLessThan(N)
    re.compile(r"(\.toBeLessThan\()(\d+)(\))"),
    # expect(x).toBeCloseTo(N)
    re.compile(r"(\.toBeCloseTo\()(\d+)(\))"),
]


def _mutate_source(source: str) -> tuple[str | None, str | None]:
    """Apply ONE mutation to the first mutable assertion in ``source``.

    Returns:
        ``(mutated_source, description)`` if a mutation was applied;
        ``(None, None)`` if no mutable assertion was found.

    The description is human-readable, e.g.
    ``"expect(x).toBe(5) → expect(x).toBe(6)"``.
    """
    lines = source.splitlines(keepends=True)

    for line_no, line in enumerate(lines):
        for pattern in _ASSERTION_PATTERNS:
            m = pattern.search(line)
            if m is None:
                continue

            before_val = m.group(2)
            # Compute the mutation.
            if before_val in ("true", "false"):
                after_val = "false" if before_val == "true" else "true"
            else:
                try:
                    after_val = str(int(before_val) + 1)
                except ValueError:
                    continue

            original_segment = m.group(0)
            mutated_segment = m.group(1) + after_val + m.group(3)
            mutated_line = line[: m.start()] + mutated_segment + line[m.end() :]
            mutated_lines = lines[:line_no] + [mutated_line] + lines[line_no + 1 :]
            mutated_source = "".join(mutated_lines)

            description = (
                f"line {line_no + 1}: {original_segment.strip()} → {mutated_segment.strip()}"
            )
            return mutated_source, description

    return None, None


# ─── Stryker JSON report parser ───────────────────────────────────────────────

# Stryker mutant status values we care about.
_STRYKER_KILLED = "Killed"
_STRYKER_SURVIVED = "Survived"
_STRYKER_TIMEOUT = "Timeout"
_STRYKER_COMPILE_ERROR = "CompileError"
_STRYKER_RUNTIME_ERROR = "RuntimeError"

# The Stryker JSON report schema (v2+):
# {
#   "schemaVersion": "2.0",
#   "mutants": [
#     { "id": "...", "status": "Killed"|"Survived"|..., ... }
#   ]
# }


def _parse_stryker_report(raw: str) -> tuple[TSMutationVerdict, dict | None]:
    """Parse Stryker's mutation report JSON.

    Args:
        raw: The stdout from a Stryker ``--reporter json`` invocation.

    Returns:
        ``(verdict, report_dict)`` where ``verdict`` is the classification of
        the first mutant and ``report_dict`` is the full parsed report (or
        ``None`` on parse failure).
    """
    if not raw.strip():
        return TSMutationVerdict.NO_MUTANT, None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Stryker might prefix the JSON with log lines; try to find the JSON block.
        json_start = raw.find("{")
        if json_start == -1:
            return TSMutationVerdict.NO_MUTANT, None
        try:
            data = json.loads(raw[json_start:])
        except json.JSONDecodeError:
            return TSMutationVerdict.NO_MUTANT, None

    mutants = data.get("mutants", [])
    if not mutants:
        return TSMutationVerdict.NO_MUTANT, data

    # Classify the first mutant.
    first = mutants[0]
    status = first.get("status", "")

    if status == _STRYKER_KILLED:
        return TSMutationVerdict.KILLED, data
    if status == _STRYKER_SURVIVED:
        return TSMutationVerdict.SURVIVED, data
    if status == _STRYKER_TIMEOUT:
        return TSMutationVerdict.TIMEOUT, data
    if status in (_STRYKER_COMPILE_ERROR, _STRYKER_RUNTIME_ERROR):
        return TSMutationVerdict.SYNTAX_ERROR, data

    # Unknown status — treat as no_mutant (inconclusive).
    return TSMutationVerdict.NO_MUTANT, data


# ─── Default Stryker config path ─────────────────────────────────────────────

_BUNDLED_STRYKER_TMPL = Path(__file__).parent / "stryker.tmpl.json"


def _build_stryker_config(
    test_file: Path,
    runner_image: str,
    tmpl_path: Path,
) -> str:
    """Render the Stryker config template with concrete values.

    Placeholders replaced:
      $TEST_FILE     — absolute path to the test file
      $RUNNER_IMAGE  — Docker image name
    """
    try:
        tmpl = tmpl_path.read_text()
    except OSError:
        # Fallback minimal config.
        tmpl = json.dumps(
            {
                "testRunner": "jest",
                "reporters": ["json"],
                "mutate": ["$TEST_FILE"],
                "coverageAnalysis": "off",
            }
        )

    return tmpl.replace("$TEST_FILE", str(test_file)).replace("$RUNNER_IMAGE", runner_image)


# ─── Public entry point ──────────────────────────────────────────────────────


def run_ts_mutate_probe(
    test_file: Path,
    project_dir: Path,
    *,
    runner_fn: Callable[..., Any] | None = None,
    runner_image: str = "pfactory-runner-jest:latest",
    stryker_config_path: Path | None = None,
    timeout: int = 180,
) -> TSMutateReport:
    """Mutate ONE assertion in ``test_file``; run with Stryker; classify.

    The probe writes a mutated copy of ``test_file`` to a temporary
    directory (NOT over the original), runs Stryker against the mutant,
    then classifies the result.

    Args:
        test_file: Absolute path to the TypeScript test file.
        project_dir: Project root.
        runner_fn: Injection point for tests.  Must accept
            ``(cmd: list[str], cwd: str, *, image: str, timeout: int)``
            and return an object with ``.returncode``, ``.stdout``,
            ``.stderr``.
        runner_image: Docker image containing Stryker + Jest.
        stryker_config_path: Path to the Stryker JSON config template.
            Defaults to the bundled ``stryker.tmpl.json``.
        timeout: Container run timeout in seconds.

    Returns:
        :class:`TSMutateReport`
    """
    if not test_file.exists():
        return TSMutateReport(
            test_file=test_file,
            verdict=TSMutationVerdict.SYNTAX_ERROR,
            mutated_assertion=None,
            raw_stryker_json=None,
        )

    try:
        source = test_file.read_text()
    except OSError as exc:
        logger.warning("Could not read %s: %s", test_file, exc)
        return TSMutateReport(
            test_file=test_file,
            verdict=TSMutationVerdict.SYNTAX_ERROR,
            mutated_assertion=None,
            raw_stryker_json=None,
        )

    # Mutate the source.
    mutated_source, mutation_desc = _mutate_source(source)
    if mutated_source is None:
        return TSMutateReport(
            test_file=test_file,
            verdict=TSMutationVerdict.NO_MUTANT,
            mutated_assertion=None,
            raw_stryker_json=None,
        )

    # Write the mutated file to a temp directory (preserves original).
    with tempfile.TemporaryDirectory(prefix="pfactory-ts-mutant-") as tmp_dir:
        mutant_file = Path(tmp_dir) / test_file.name
        try:
            mutant_file.write_text(mutated_source)
        except OSError as exc:
            logger.warning("Could not write mutant to %s: %s", mutant_file, exc)
            return TSMutateReport(
                test_file=test_file,
                verdict=TSMutationVerdict.SYNTAX_ERROR,
                mutated_assertion=mutation_desc,
                raw_stryker_json=None,
            )

        # Build Stryker config.
        tmpl_path = stryker_config_path or _BUNDLED_STRYKER_TMPL
        config_str = _build_stryker_config(mutant_file, runner_image, tmpl_path)
        config_file = Path(tmp_dir) / "stryker.config.json"
        config_file.write_text(config_str)

        cmd = [
            "npx",
            "stryker",
            "run",
            "--config",
            str(config_file),
            "--reporters",
            "json",
            "--logLevel",
            "error",
        ]
        cwd = str(project_dir)

        try:
            if runner_fn is None:
                from tools.runners.docker_runner import DockerRunner

                runner = DockerRunner(image=runner_image, timeout=timeout)
                result = runner.run(cmd, cwd=cwd)
            else:
                result = runner_fn(cmd, cwd, image=runner_image, timeout=timeout)
        except Exception as exc:
            logger.warning("Stryker runner raised: %s", exc)
            return TSMutateReport(
                test_file=test_file,
                verdict=TSMutationVerdict.SYNTAX_ERROR,
                mutated_assertion=mutation_desc,
                raw_stryker_json=None,
            )

        raw_output = result.stdout or ""
        verdict, stryker_data = _parse_stryker_report(raw_output)

        return TSMutateReport(
            test_file=test_file,
            verdict=verdict,
            mutated_assertion=mutation_desc,
            raw_stryker_json=stryker_data,
        )
