"""Tests for the PFactory Planner prompt-assembly helpers — Task 5 (#6) commit 3.

The helpers are pure-string assembly: load a `.md` file from
`apps/backend/prompts/`, prepend a context block with concrete paths.
No SDK involvement; tests stay fast and offline.

Covered:
  - get_pfactory_planner_prompt builds an initial-mode prompt
  - get_pfactory_planner_replan_prompt builds a replan-mode prompt
  - Both inject the concrete spec_dir + project_dir
  - Both raise FileNotFoundError if the .md is missing
  - The initial prompt mentions the required schema keys
  - The replan prompt warns against rewriting earlier phases
  - Output is reasonably sized (no runaway concatenation bugs)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Inherited prompts_pkg imports providers + SDK; pre-mock to stay offline.
for _m in [
    "claude_agent_sdk", "claude_agent_sdk.types",
    "core.client", "phase_config", "providers.factory",
]:
    sys.modules.setdefault(_m, MagicMock())

from prompts_pkg.prompts import (  # noqa: E402
    get_pfactory_planner_prompt,
    get_pfactory_planner_replan_prompt,
)

# ── Path injection ──────────────────────────────────────────────────────


def test_initial_includes_spec_dir() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "/ws/demo/001" in p


def test_initial_includes_project_dir() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "/proj" in p


def test_initial_has_spec_context_header() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "SPEC CONTEXT" in p


def test_initial_references_context_files() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "aifactory_spec.md" in p
    assert "diff.patch" in p
    assert "source.json" in p


def test_initial_points_at_test_plan_json_for_write() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "test_plan.json" in p


# ── Body content (proves the file is loaded, not just the context block) ──


def test_initial_contains_subtask_schema_keys() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    for key in ("target", "rationale", "files_to_create", "verification"):
        assert key in p, f"missing schema key in prompt: {key}"


def test_initial_warns_against_anti_patterns() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "Anti-patterns" in p or "anti-pattern" in p.lower()


def test_initial_mentions_subtask_cap() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "30" in p  # the hard cap


def test_initial_mentions_lane_functional() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "functional" in p.lower()


# ── Replan mode ─────────────────────────────────────────────────────────


def test_replan_includes_spec_dir() -> None:
    p = get_pfactory_planner_replan_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "/ws/demo/001" in p


def test_replan_has_replan_context_header() -> None:
    p = get_pfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert "REPLAN CONTEXT" in p


def test_replan_references_replan_request_json() -> None:
    p = get_pfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert "replan_request.json" in p


def test_replan_warns_against_rewriting_earlier_phases() -> None:
    p = get_pfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    # Either the explicit instruction or a strong hint must be present
    assert (
        "do not rewrite earlier phases" in p.lower()
        or "do not edit earlier phases" in p.lower()
        or "preserve every existing phase" in p.lower()
    )


def test_replan_mentions_bumping_replan_count() -> None:
    p = get_pfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert "replan_count" in p


def test_replan_mentions_one_corrected_subtask() -> None:
    p = get_pfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert "one corrected subtask" in p.lower() or "exactly one" in p.lower()


# ── Reasonable size + structure ──────────────────────────────────────────


def test_initial_size_in_expected_range() -> None:
    p = get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    # Body ~8 KB + context block; combined should be 5 KB – 15 KB
    assert 5000 < len(p) < 15000, f"unexpected size: {len(p)}"


def test_replan_size_in_expected_range() -> None:
    p = get_pfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert 3000 < len(p) < 10000, f"unexpected size: {len(p)}"


def test_initial_and_replan_are_distinct_prompts() -> None:
    initial = get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    replan = get_pfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))
    assert initial != replan
    assert "initial mode" in initial.lower()
    assert "replan mode" in replan.lower()


# ── Missing-file fail-fast ──────────────────────────────────────────────


def test_initial_raises_when_md_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If planner.md is missing, FileNotFoundError surfaces clearly."""
    import prompts_pkg.prompts as mod
    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path)  # empty dir
    with pytest.raises(FileNotFoundError, match="planner.md"):
        get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))


def test_replan_raises_when_md_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import prompts_pkg.prompts as mod
    monkeypatch.setattr(mod, "PROMPTS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="planner_replan.md"):
        get_pfactory_planner_replan_prompt(Path("/ws/x"), Path("/p"))


# =============================================================================
# Task 5 / #21 — Framework registry + catalog injection tests
# =============================================================================


def test_planner_prompt_includes_framework_registry_block() -> None:
    """Prompt must contain a ## FRAMEWORK REGISTRY block with all three frameworks."""
    p = get_pfactory_planner_prompt(Path("/ws/demo/001"), Path("/proj"))
    assert "## FRAMEWORK REGISTRY" in p
    assert "playwright" in p
    assert "jest" in p
    assert "pytest" in p


def test_planner_prompt_registry_block_shows_language_and_lanes() -> None:
    """Each registry entry must note language= and lanes= for the agent."""
    p = get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "language=" in p
    assert "lanes=" in p


def test_planner_prompt_includes_catalog_block_when_absent(tmp_path: Path) -> None:
    """When no catalog file exists the block says 'no catalog at this repo yet'."""
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()
    p = get_pfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "## TESTS CATALOG" in p
    assert "no catalog at this repo yet" in p


def test_planner_prompt_includes_catalog_block_when_present(tmp_path: Path) -> None:
    """When a catalog file exists the block lists entries with test_id and covers_acs."""
    import json

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()

    catalog_data = {
        "version": 1,
        "updated_at": "2026-05-28T00:00:00Z",
        "tests": [
            {
                "test_id": "ac1-login-flow",
                "test_file": "tests/e2e/login.spec.ts",
                "framework": "playwright",
                "lane": "browser",
                "language": "typescript",
                "covers_acs": ["AC#1: User can log in"],
                "generated_at": "2026-05-28T00:00:00Z",
                "generated_by_task": "001",
                "last_verdict": "accept",
            }
        ],
    }
    (spec_dir / "context" / "tests_catalog.json").write_text(json.dumps(catalog_data))

    p = get_pfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "## TESTS CATALOG" in p
    assert "ac1-login-flow" in p
    assert "covers_acs" in p or "AC#1: User can log in" in p


def test_planner_prompt_catalog_block_shows_framework_and_lane(tmp_path: Path) -> None:
    """Catalog block lines include framework= and lane= for the agent."""
    import json

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()

    catalog_data = {
        "version": 1,
        "updated_at": "2026-05-28T00:00:00Z",
        "tests": [
            {
                "test_id": "tc-1",
                "test_file": "tests/test_foo.py",
                "framework": "pytest",
                "lane": "unit",
                "language": "python",
                "covers_acs": ["AC#3: returns 200"],
                "generated_at": "2026-05-28T00:00:00Z",
                "generated_by_task": "002",
                "last_verdict": "accept",
            }
        ],
    }
    (spec_dir / "context" / "tests_catalog.json").write_text(json.dumps(catalog_data))

    p = get_pfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "framework=pytest" in p
    assert "lane=unit" in p


def test_planner_prompt_catalog_block_shows_total_count(tmp_path: Path) -> None:
    """Catalog block footer line reports total entry count."""
    import json

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()

    tests = []
    for i in range(3):
        tests.append({
            "test_id": f"tc-{i}",
            "test_file": f"tests/test_{i}.py",
            "framework": "pytest",
            "lane": "unit",
            "language": "python",
            "covers_acs": [f"AC#{i}"],
            "generated_at": "2026-05-28T00:00:00Z",
            "generated_by_task": "003",
            "last_verdict": "accept",
        })
    (spec_dir / "context" / "tests_catalog.json").write_text(
        json.dumps({"version": 1, "updated_at": "2026-05-28T00:00:00Z", "tests": tests})
    )

    p = get_pfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "3 entries total" in p


def test_planner_prompt_catalog_block_flags_operator_locked(tmp_path: Path) -> None:
    """Locked catalog entries must be marked [operator_locked] in the block."""
    import json

    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "context").mkdir()

    catalog_data = {
        "version": 1,
        "updated_at": "2026-05-28T00:00:00Z",
        "tests": [
            {
                "test_id": "locked-test",
                "test_file": "tests/locked.py",
                "framework": "pytest",
                "lane": "unit",
                "language": "python",
                "covers_acs": ["AC#5: locked"],
                "generated_at": "2026-05-28T00:00:00Z",
                "generated_by_task": "004",
                "last_verdict": "accept",
                "operator_locked": True,
            }
        ],
    }
    (spec_dir / "context" / "tests_catalog.json").write_text(json.dumps(catalog_data))

    p = get_pfactory_planner_prompt(spec_dir, tmp_path / "proj")
    assert "operator_locked" in p


def test_planner_prompt_mentions_intent_create_update_skip() -> None:
    """Prompt body must mention all three intent values."""
    p = get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "intent" in p
    assert "create" in p
    assert "update" in p
    assert "skip" in p


def test_planner_prompt_mentions_pfactory_yml_and_catalog_context_files() -> None:
    """SPEC CONTEXT block must list both new v0.2 context files."""
    p = get_pfactory_planner_prompt(Path("/ws/x"), Path("/p"))
    assert "pfactory_yml.json" in p
    assert "tests_catalog.json" in p
