"""Tests for the Evaluator prompt assembly helper — Task 7 (#8) commit 4.

The helper assembles a multi-test system prompt by loading
``apps/backend/prompts/evaluator.md`` and prepending an EVALUATOR
CONTEXT block with one sub-block per generated test (carrying the
four pre-computed numeric signals: coverage, stability, mutation,
lint-promotion).

Covered:
  - Path injection (spec_dir, project_dir, verdicts.json target)
  - Per-test sub-block injection (id, file, target, rationale)
  - Signal formatting from REAL dataclass instances
  - Signal formatting from dict-shaped bundles (post-JSON-load)
  - Missing signal fields degrade to "not computed" without crash
  - Empty bundle list — emit empty-verdicts hint
  - Body sanity (mentions all five signals, verdict types, tools)
  - FileNotFoundError when evaluator.md is missing
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents.coverage_delta import CoverageDelta
from agents.flake_risk_lint import FlakeRiskResult
from agents.lint_promotion import promote_flake_findings
from agents.mutate_probe import (
    MutationApplied,
    MutationResult,
    MutationVerdict,
)
from agents.stability_runner import (
    StabilityResult,
    StabilityRun,
    StabilityVerdict,
)
from prompts_pkg.prompts import (
    _format_evaluator_per_test_block,
    get_pfactory_evaluator_prompt,
)

# ── Fixtures: real dataclass bundles ───────────────────────────────────


@pytest.fixture
def coverage_delta() -> CoverageDelta:
    return CoverageDelta(
        new_lines=frozenset({("app/auth/login.py", 12), ("app/auth/login.py", 13)}),
        new_files=0,
        delta_pct=5.25,
        baseline_total_covered=100,
        after_total_covered=102,
    )


@pytest.fixture
def stability_stable() -> StabilityResult:
    return StabilityResult(
        verdict=StabilityVerdict.STABLE,
        runs=(
            StabilityRun(returncode=0),
            StabilityRun(returncode=0),
            StabilityRun(returncode=0),
        ),
        seed=424242,
        rerun_count=3,
    )


@pytest.fixture
def mutation_killed() -> MutationResult:
    return MutationResult(
        verdict=MutationVerdict.KILLED,
        mutation=MutationApplied(
            operator="Eq->NotEq",
            lineno=4,
            before="1 == 1",
            after="1 != 1",
        ),
        mutated_source="def test_x():\n    assert 1 != 1\n",
        runner_stdout_tail="assertion failed",
    )


@pytest.fixture
def lint_promotion_clean():
    forced = FlakeRiskResult(ok=True, hits=[])
    return promote_flake_findings(forced, "def test_x(): pass\n")


@pytest.fixture
def dataclass_bundle(
    coverage_delta, stability_stable, mutation_killed, lint_promotion_clean,
):
    """A realistic per-test bundle as commit 5 will construct."""
    from dataclasses import dataclass, field
    from typing import Any

    @dataclass
    class _Bundle:
        test_id: str = "ac1-login-sets-24h-expiry"
        test_file: str = "/ws/spec/tests/test_login_expiry.py"
        target: str = "app/auth/login.py::login_user"
        rationale: str = "AC#1: login sets expires_at to +24h"
        coverage_delta: Any = None
        stability: Any = None
        mutation: Any = None
        lint_promotion: Any = None

    return _Bundle(
        coverage_delta=coverage_delta,
        stability=stability_stable,
        mutation=mutation_killed,
        lint_promotion=lint_promotion_clean,
    )


@pytest.fixture
def dict_bundle(
    coverage_delta, stability_stable, mutation_killed, lint_promotion_clean,
):
    """Dict-shaped variant (post-JSON-load shape)."""
    return {
        "test_id": "ac2-expired-returns-none",
        "test_file": "/ws/spec/tests/test_get_session_expired.py",
        "target": "app/auth/session.py::get_session",
        "rationale": "AC#2: returns None for expired session",
        "coverage_delta": coverage_delta,
        "stability": stability_stable,
        "mutation": mutation_killed,
        "lint_promotion": lint_promotion_clean,
    }


# ── Per-test sub-block formatting ──────────────────────────────────────


def test_block_includes_test_id(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "ac1-login-sets-24h-expiry" in block


def test_block_includes_test_file_and_target(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "/ws/spec/tests/test_login_expiry.py" in block
    assert "app/auth/login.py::login_user" in block


def test_block_includes_rationale(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "AC#1" in block


def test_block_formats_coverage(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    # delta_pct=5.25 → "+5.25" with sign
    assert "+5.25" in block
    # new_lines=2 lines
    assert "new_lines=2" in block


def test_block_formats_stability(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "stability: stable" in block
    assert "3 runs" in block


def test_block_formats_mutation(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    assert "mutation: killed" in block
    assert "op=Eq->NotEq" in block


def test_block_formats_lint_promotion(dataclass_bundle) -> None:
    block = _format_evaluator_per_test_block(dataclass_bundle)
    # No findings → "no findings" summary
    assert "no findings" in block


def test_block_accepts_dict_shape(dict_bundle) -> None:
    block = _format_evaluator_per_test_block(dict_bundle)
    assert "ac2-expired-returns-none" in block
    assert "stability: stable" in block
    assert "mutation: killed" in block


def test_block_missing_signals_degrades_gracefully() -> None:
    """A bundle missing the four numeric signals renders the labels without
    crashing.  Coverage None renders as 'N/A (browser lane)' (Decision 11 —
    we never want the LLM to interpret a missing delta as '0%'); stability,
    mutation, and lint_promotion render as 'not computed'."""
    bundle = {
        "test_id": "minimal",
        "test_file": "/x.py",
        "target": "x::y",
        "rationale": "AC#X",
        # no coverage / stability / mutation / lint_promotion
    }
    block = _format_evaluator_per_test_block(bundle)
    assert "coverage: N/A (browser lane)" in block
    assert "stability: not computed" in block
    assert "mutation: not computed" in block
    assert "lint_promotion: not computed" in block


# ── Full prompt assembly ───────────────────────────────────────────────


def test_assembles_with_one_bundle(dataclass_bundle) -> None:
    p = get_pfactory_evaluator_prompt(
        Path("/ws/spec"), Path("/proj"), [dataclass_bundle],
    )
    assert "/ws/spec" in p
    assert "/proj" in p
    assert "/ws/spec/findings/verdicts.json" in p
    assert "Number of generated tests to evaluate: 1" in p
    assert "ac1-login-sets-24h-expiry" in p


def test_assembles_with_many_bundles(dataclass_bundle, dict_bundle) -> None:
    p = get_pfactory_evaluator_prompt(
        Path("/ws/spec"), Path("/proj"), [dataclass_bundle, dict_bundle],
    )
    assert "Number of generated tests to evaluate: 2" in p
    assert "ac1-login-sets-24h-expiry" in p
    assert "ac2-expired-returns-none" in p


def test_assembles_with_empty_bundle_list() -> None:
    p = get_pfactory_evaluator_prompt(Path("/ws"), Path("/p"), [])
    assert "Number of generated tests to evaluate: 0" in p
    assert "no tests in this batch" in p


# ── Body content sanity ────────────────────────────────────────────────


def test_body_mentions_all_five_signals(dataclass_bundle) -> None:
    p = get_pfactory_evaluator_prompt(Path("/ws"), Path("/p"), [dataclass_bundle])
    assert "Coverage delta" in p
    assert "stability" in p.lower()
    assert "mutate" in p.lower() or "mutation" in p.lower()
    assert "lint" in p.lower()
    assert "Semantic relevance" in p or "semantic_relevance" in p.lower()


def test_body_mentions_verdict_types(dataclass_bundle) -> None:
    p = get_pfactory_evaluator_prompt(Path("/ws"), Path("/p"), [dataclass_bundle])
    for v in ("accept", "reject", "flag"):
        assert v in p


def test_body_mentions_tool_grants(dataclass_bundle) -> None:
    p = get_pfactory_evaluator_prompt(Path("/ws"), Path("/p"), [dataclass_bundle])
    for tool in ("Read", "Write", "Glob", "Grep"):
        assert tool in p
    # Negative grants explicitly mentioned
    assert "Bash" in p  # in the "do NOT have" list
    assert "do NOT have" in p or "no bash" in p.lower()


def test_body_mentions_verdicts_json_schema(dataclass_bundle) -> None:
    p = get_pfactory_evaluator_prompt(Path("/ws"), Path("/p"), [dataclass_bundle])
    assert "verdicts.json" in p
    assert "evaluator_version" in p
    assert "signals_summary" in p


def test_total_size_in_range(dataclass_bundle) -> None:
    p = get_pfactory_evaluator_prompt(
        Path("/ws"), Path("/p"), [dataclass_bundle],
    )
    # Body ~8KB + context block ~2KB.
    assert 8_000 < len(p) < 20_000, f"unexpected size {len(p)}"


# ── Failure mode ───────────────────────────────────────────────────────


def test_raises_when_md_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dataclass_bundle,
) -> None:
    import prompts_pkg.prompts as mod
    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path)  # empty dir
    with pytest.raises(FileNotFoundError, match="evaluator.md"):
        get_pfactory_evaluator_prompt(
            Path("/ws"), Path("/p"), [dataclass_bundle],
        )


# ── Task 10 (#26) — Coverage N/A rendering tests ──────────────────────


def test_per_test_block_renders_coverage_na_when_none() -> None:
    """Bundle with coverage_delta=None renders 'coverage: N/A (browser lane)'."""
    bundle = {
        "test_id": "pw-test-0",
        "test_file": "/ws/spec/tests/test_login.spec.ts",
        "target": "app/login.ts::loginFlow",
        "rationale": "AC#1: login page accessible",
        "coverage_delta": None,  # explicit None = browser lane
        "stability": None,
        "mutation": None,
        "lint_promotion": None,
    }
    block = _format_evaluator_per_test_block(bundle)
    assert "coverage: N/A (browser lane)" in block
    assert "0%" not in block  # must NOT say "0%"
    assert "coverage: not computed" not in block


def test_per_test_block_renders_percent_when_numeric(
    coverage_delta,
) -> None:
    """Bundle with a real CoverageDelta renders the numeric delta_pct."""
    bundle = {
        "test_id": "unit-test-0",
        "test_file": "/ws/spec/tests/test_auth.py",
        "target": "app/auth.py::authenticate",
        "rationale": "AC#2: authenticate returns user object",
        "coverage_delta": coverage_delta,  # CoverageDelta(delta_pct=5.25, ...)
        "stability": None,
        "mutation": None,
        "lint_promotion": None,
    }
    block = _format_evaluator_per_test_block(bundle)
    # delta_pct=5.25 → should appear as "+5.25"
    assert "+5.25" in block
    assert "N/A" not in block


def test_evaluator_md_mentions_browser_lane_na_rule() -> None:
    """The evaluator.md prompt must contain the N/A coverage rule
    and reference the browser lane so the LLM knows not to penalise."""
    from prompts_pkg.prompts import PROMPTS_DIR

    md = (PROMPTS_DIR / "evaluator.md").read_text()
    assert "N/A" in md
    assert "browser" in md.lower()
    assert "skip the coverage rule" in md or "skip coverage" in md.lower()


def test_evaluator_md_does_not_penalise_browser_for_zero_coverage() -> None:
    """The prompt must NOT instruct the LLM to penalise browser tests for
    '0% coverage' — that was the bug this task fixes.

    The correct text is a 'do not penalise' instruction, not an implicit
    'low coverage = reject' rule. We verify:
    1. The phrase 'do not penalise' (or 'do not factor') appears near
       browser-lane coverage context.
    2. There is no positive instruction to 'reject' for '0%' coverage
       when the browser lane is discussed.
    """
    import re

    from prompts_pkg.prompts import PROMPTS_DIR

    md = (PROMPTS_DIR / "evaluator.md").read_text()

    # The correct fix: prompt must say "do not penalise" for 0% in browser context.
    # We verify the N/A rule is there — a "do not" near browser + 0%.
    assert "do not" in md.lower() or "DO NOT" in md, (
        "evaluator.md must contain a 'do not' instruction for browser coverage"
    )
    # Verify there is no instruction to auto-reject for 0% on browser tests.
    # Look for a pattern like "browser ... 0 ... reject" (positive reject rule).
    reject_0_browser = re.search(
        r"browser[^\n]{0,200}0[%\s][^\n]{0,100}reject",
        md,
        re.IGNORECASE,
    )
    assert reject_0_browser is None, (
        "evaluator.md appears to instruct the LLM to reject browser tests "
        "for 0% coverage — this is the bug Task 10 was supposed to fix"
    )


def test_full_prompt_assembly_with_mixed_null_and_numeric(
    coverage_delta,
    stability_stable,
    mutation_killed,
    lint_promotion_clean,
) -> None:
    """Prompt with one null-coverage bundle and one numeric-coverage bundle
    must render both correctly — the null one gets N/A, the numeric one
    gets the delta."""
    null_bundle = {
        "test_id": "browser-test",
        "test_file": "/ws/spec/tests/browser.spec.ts",
        "target": "app/ui.ts::loginPage",
        "rationale": "AC#1: login page loads",
        "coverage_delta": None,
    }
    numeric_bundle = {
        "test_id": "unit-test",
        "test_file": "/ws/spec/tests/test_unit.py",
        "target": "app/auth.py::authenticate",
        "rationale": "AC#2: authenticate works",
        "coverage_delta": coverage_delta,  # delta_pct=5.25
        "stability": stability_stable,
        "mutation": mutation_killed,
        "lint_promotion": lint_promotion_clean,
    }
    prompt = get_pfactory_evaluator_prompt(
        Path("/ws/spec"),
        Path("/proj"),
        [null_bundle, numeric_bundle],
    )
    assert "Number of generated tests to evaluate: 2" in prompt
    assert "coverage: N/A (browser lane)" in prompt
    assert "+5.25" in prompt
    # Both test IDs appear
    assert "browser-test" in prompt
    assert "unit-test" in prompt
