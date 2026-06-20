"""Per-test signal bundle + the individual signal primitives.

Extracted from ``agents/evaluator.py`` (issue #193 god-file split).
``EvaluatorSignals`` is the per-test bundle the prompt helper formats, and the
``_*_for_subtask`` helpers each compute one signal (coverage delta, 3x stability,
mutation, lint promotion, cross-run flaky history). The coverage-strategy lookup
also lives here since coverage is the only signal that branches on the framework
descriptor.

Behaviour is identical to the original module: the per-signal logic is a verbatim
move; the only changes are mechanical (module-level imports replacing the former
function-local imports, and ambiguous-unicode docstring fixes).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.coverage_delta import compute_delta_from_paths
from agents.flake_risk_lint import flake_risk_lint
from agents.flaky_history import record_outcome
from agents.lanes.common import _eval_log
from agents.lint_promotion import promote_flake_findings
from agents.mutation_dispatch import (
    is_mutation_supported,
    mutant_extension,
    run_language_mutation,
)
from agents.stability_runner import StabilityVerdict, check_stability
from framework_registry import load_registry

# ─── Per-test signal bundle ─────────────────────────────────────────────


@dataclass
class EvaluatorSignals:
    """Per-test bundle of the four pre-computed signals plus identity.

    The fifth signal (semantic relevance) is the LLM's call — it
    doesn't live in this dataclass.

    Any of the four signal fields can be ``None`` if the primitive
    couldn't run (e.g., coverage XML not emitted by the Executor for
    this test). The prompt helper renders missing signals as
    "not computed" rather than crashing.

    ``coverage_delta`` is explicitly ``None`` (not zero) when the
    framework's ``coverage_strategy == "skip"`` (Decision 11 — Browser
    lane).  This prevents the Evaluator prompt from seeing "0% coverage"
    and issuing a spurious reject for Playwright tests.  A ``None``
    value is rendered as "N/A (browser lane)" by
    ``_format_evaluator_per_test_block``.
    """

    test_id: str
    test_file: Path
    target: str
    rationale: str
    coverage_delta: Any = None  # CoverageDelta | None  (None = skip-coverage lane)
    stability: Any = None  # StabilityResult | None
    mutation: Any = None  # MutationResult | None
    lint_promotion: Any = None  # PromotionResult | None
    flaky_history: Any = None  # FlakyHistory | None  (cross-run flip-rate, #37)


# ─── Coverage-strategy lookup ───────────────────────────────────────────


def _framework_coverage_strategy(subtask: dict) -> str | None:
    """Look up the framework descriptor's coverage_strategy for a subtask.

    Returns the strategy string ("lcov", "cobertura", "skip") or None
    if the subtask has no ``framework`` field or the registry lookup
    fails (e.g. unknown framework name — v0.1 back-compat).

    Failures are swallowed and logged at DEBUG level so a registry
    misconfiguration never blocks the Evaluator.
    """
    framework_name = subtask.get("framework")
    if not framework_name:
        return None
    try:
        registry = load_registry()
        desc = registry.get(framework_name)
        if desc is None:
            _eval_log.debug(
                "coverage_strategy: framework %r not in registry; treating as numeric",
                framework_name,
            )
            return None
        return desc.coverage_strategy
    except Exception as exc:  # noqa: BLE001 — never block the Evaluator
        _eval_log.debug(
            "coverage_strategy lookup failed for framework %r: %s",
            framework_name,
            exc,
        )
        return None


# ─── Per-signal primitives ──────────────────────────────────────────────


def _coverage_delta_for_subtask(
    spec_dir: Path,
    subtask: dict,
):
    """Try to compute coverage delta for one test.

    Returns ``None`` in two distinct cases:

    1. **Skip-coverage lane** (Decision 11): the subtask's framework has
       ``coverage_strategy == "skip"`` (e.g. Playwright Browser lane).
       The Evaluator prompt renders this as "N/A (browser lane)" and does
       NOT penalise the test for zero coverage.

    2. **Coverage XML absent**: baseline or per-test coverage.xml are
       missing — the LLM will see "not computed" (pre-existing behaviour).

    Looks for ``spec_dir/findings/baseline_coverage.xml`` and
    ``spec_dir/findings/runs/<test_id>/coverage.xml`` for case (2).
    """
    # Case 1: framework explicitly opted out of coverage measurement.
    strategy = _framework_coverage_strategy(subtask)
    if strategy == "skip":
        _eval_log.debug(
            "coverage_delta: framework %r uses skip strategy — returning None",
            subtask.get("framework"),
        )
        return None

    # Case 2: try to parse XML coverage artefacts.
    baseline = spec_dir / "findings" / "baseline_coverage.xml"
    after = spec_dir / "findings" / "runs" / subtask["id"] / "coverage.xml"
    if not baseline.exists() or not after.exists():
        return None
    try:
        return compute_delta_from_paths(baseline, after)
    except Exception as exc:  # noqa: BLE001 — defensive
        _eval_log.warning(
            "coverage_delta failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _stability_for_subtask(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
):
    """Run the 3x stability check for one test."""
    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    try:
        return check_stability(test_file, project_dir, runner_fn)
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "stability check failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _flaky_history_for_subtask(spec_dir: Path, subtask: dict, stability):
    """Record this run's pass/fail outcome into the project-level flaky
    history store and return the updated FlakyHistory (#37).

    The outcome is derived from the 3x stability verdict: ``STABLE`` is a
    clean pass; anything else (flaky / consistent-fail / error) is a fail.
    Returns ``None`` when stability couldn't run, so we don't pollute the
    history with a phantom outcome. The store lives one level above the
    spec dir (``<workspace>/<project>/test_history.json``) so it persists
    across separate spec runs of the same project.
    """
    if stability is None:
        return None
    try:
        store = spec_dir.parent.parent / "test_history.json"
        passed = stability.verdict == StabilityVerdict.STABLE
        return record_outcome(store, subtask["id"], passed)
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "flaky-history record failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _mutation_for_subtask(
    spec_dir: Path,
    project_dir: Path,
    subtask: dict,
    runner_fn,
):
    """Run the mutate-and-check probe for one test, dispatched by language.

    Routes to the Python (mutmut-style AST) or TypeScript (Stryker) backend
    via ``mutation_dispatch`` (#41). Writes the mutant to
    ``spec_dir/findings/mutants/<test_id>.<ext>`` so the original test file
    stays clean. Returns ``None`` for languages with no wired backend.
    """
    language = subtask.get("language")
    if not is_mutation_supported(language):
        return None
    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    ext = mutant_extension(language)
    mutant_path = spec_dir / "findings" / "mutants" / f"{subtask['id']}.{ext}"
    try:
        return run_language_mutation(
            language, test_file, project_dir, runner_fn, mutant_path=mutant_path
        )
    except Exception as exc:  # noqa: BLE001
        _eval_log.warning(
            "mutate probe failed for %s: %s",
            subtask["id"],
            exc,
        )
        return None


def _lint_promotion_for_subtask(spec_dir: Path, subtask: dict):
    """Run flake_risk_lint + promote findings for one test."""
    test_file = spec_dir / subtask["files_to_create"][0]
    if not test_file.exists():
        return None
    try:
        source = test_file.read_text()
    except OSError:
        return None
    result = flake_risk_lint(source)
    return promote_flake_findings(result, source)
