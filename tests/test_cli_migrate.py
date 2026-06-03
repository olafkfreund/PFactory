"""Tests for `cli migrate v0_1_catalog` — the v0.1 workspace migration CLI.

Task 15 / #31 commit 5.

All tests use temporary directories; no network or LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cli import pfactory_main
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_spec_dir(workspace: Path, project: str, spec: str) -> Path:
    """Create a minimal v0.1 spec directory structure."""
    spec_dir = workspace / project / "specs" / spec
    spec_dir.mkdir(parents=True, exist_ok=True)
    return spec_dir


def add_test_file(spec_dir: Path, name: str, content: str = "") -> Path:
    """Add a test_*.py file to spec_dir/tests/."""
    tests_dir = spec_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    path = tests_dir / name
    path.write_text(content or f"def test_{name[5:-3]}(): pass\n", encoding="utf-8")
    return path


def run_migrate(workspace: Path, *extra_args: str) -> Result:
    """Invoke `cli migrate v0_1_catalog` via the click test runner."""
    runner = CliRunner()
    result = runner.invoke(
        pfactory_main,
        ["migrate", "v0_1_catalog", "--workspace", str(workspace), *extra_args],
        catch_exceptions=False,
    )
    return result


# ---------------------------------------------------------------------------
# Core migration scenarios
# ---------------------------------------------------------------------------


class TestMigrateV01Catalog:
    def test_dry_run_prints_plan_without_writing(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        spec_dir = make_spec_dir(ws, "proj-a", "001-login")
        add_test_file(spec_dir, "test_login.py")

        result = run_migrate(ws, "--dry-run")
        assert result.exit_code == 0, result.output
        assert "dry-run" in result.output.lower() or "dry_run" in result.output.lower() or "Dry-run" in result.output

        # No catalog should have been written
        catalog_path = ws / "proj-a" / ".pfactory" / "tests-catalog.json"
        assert not catalog_path.exists(), "dry-run should not write catalog"

    def test_writes_consolidated_catalog(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        spec_dir = make_spec_dir(ws, "proj-a", "001-login")
        add_test_file(spec_dir, "test_login_flow.py")
        add_test_file(spec_dir, "test_session_expiry.py")

        result = run_migrate(ws)
        assert result.exit_code == 0, result.output

        # Catalog should be written alongside the project directory (fallback path)
        catalog_path = ws / "proj-a" / ".pfactory" / "tests-catalog.json"
        assert catalog_path.exists(), f"catalog not written at {catalog_path}"

        data = json.loads(catalog_path.read_text())
        assert data["version"] == 1
        test_ids = {t["test_id"] for t in data["tests"]}
        assert "test-login-flow" in test_ids
        assert "test-session-expiry" in test_ids

    def test_handles_empty_workspace(self, tmp_path: Path) -> None:
        ws = tmp_path / "empty_ws"
        ws.mkdir()

        result = run_migrate(ws)
        assert result.exit_code == 0, result.output
        assert "nothing" in result.output.lower() or "No project" in result.output

    def test_workspace_root_does_not_exist(self, tmp_path: Path) -> None:
        ws = tmp_path / "nonexistent"
        result = run_migrate(ws)
        assert result.exit_code == 0, result.output
        # Should report nothing to migrate, not crash
        assert "nothing" in result.output.lower() or "does not exist" in result.output.lower() or "not exist" in result.output.lower()

    def test_handles_spec_with_no_tests_dir(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        # Create spec dir but no tests/ subdirectory
        spec_dir = make_spec_dir(ws, "proj-a", "001-no-tests")
        # No test files added

        result = run_migrate(ws)
        assert result.exit_code == 0, result.output
        # Should not error; should report nothing to migrate
        catalog_path = ws / "proj-a" / ".pfactory" / "tests-catalog.json"
        assert not catalog_path.exists(), "Should not create catalog if no tests"

    def test_dedups_across_specs(self, tmp_path: Path) -> None:
        """Same test_id in 2 specs → only one entry in the catalog."""
        ws = tmp_path / "ws"

        # Two specs with the same test filename → same test_id
        spec1 = make_spec_dir(ws, "proj-a", "001-spec")
        add_test_file(spec1, "test_duplicate.py")

        spec2 = make_spec_dir(ws, "proj-a", "002-spec")
        add_test_file(spec2, "test_duplicate.py")  # same stem → same test_id

        result = run_migrate(ws)
        assert result.exit_code == 0, result.output

        catalog_path = ws / "proj-a" / ".pfactory" / "tests-catalog.json"
        assert catalog_path.exists()
        data = json.loads(catalog_path.read_text())
        test_ids = [t["test_id"] for t in data["tests"]]
        # Should have only one entry for test-duplicate
        assert test_ids.count("test-duplicate") == 1

    def test_multiple_specs_accumulated(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        spec1 = make_spec_dir(ws, "proj-a", "001-login")
        add_test_file(spec1, "test_login.py")

        spec2 = make_spec_dir(ws, "proj-a", "002-signup")
        add_test_file(spec2, "test_signup.py")

        result = run_migrate(ws)
        assert result.exit_code == 0, result.output

        catalog_path = ws / "proj-a" / ".pfactory" / "tests-catalog.json"
        data = json.loads(catalog_path.read_text())
        test_ids = {t["test_id"] for t in data["tests"]}
        assert "test-login" in test_ids
        assert "test-signup" in test_ids

    def test_unknown_kind_exits_nonzero(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        runner = CliRunner()
        result = runner.invoke(
            pfactory_main,
            ["migrate", "unknown_kind", "--workspace", str(ws)],
            catch_exceptions=False,
        )
        assert result.exit_code != 0

    def test_migration_sets_lane_unit(self, tmp_path: Path) -> None:
        """Migrated v0.1 entries should have lane='unit'."""
        ws = tmp_path / "ws"
        spec_dir = make_spec_dir(ws, "proj-a", "001-login")
        add_test_file(spec_dir, "test_login_flow.py")

        run_migrate(ws)

        catalog_path = ws / "proj-a" / ".pfactory" / "tests-catalog.json"
        data = json.loads(catalog_path.read_text())
        for entry in data["tests"]:
            assert entry["lane"] == "unit"

    def test_migration_sets_framework_pytest(self, tmp_path: Path) -> None:
        """Migrated v0.1 entries should have framework='pytest'."""
        ws = tmp_path / "ws"
        spec_dir = make_spec_dir(ws, "proj-a", "001-spec")
        add_test_file(spec_dir, "test_something.py")

        run_migrate(ws)

        catalog_path = ws / "proj-a" / ".pfactory" / "tests-catalog.json"
        data = json.loads(catalog_path.read_text())
        for entry in data["tests"]:
            assert entry["framework"] == "pytest"
            assert entry["language"] == "python"
