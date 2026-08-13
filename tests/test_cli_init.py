"""Tests for `cli init` — the .pfactory.yml scaffolder.

Task 15 / #31 commit 5.

Tests run entirely in temporary directories; no network or LLM calls.
The pfactory_yml validation is tested via the real load_pfactory_yml()
parser when the package is importable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from cli import pfactory_main
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_init(tmp_path: Path, *args: str, input: str = "") -> Result:
    """Invoke `cli init` in *tmp_path* via the click test runner."""
    runner = CliRunner()
    result = runner.invoke(
        pfactory_main,
        ["init", "--repo-root", str(tmp_path), *args],
        input=input,
        catch_exceptions=False,
    )
    return result


# ---------------------------------------------------------------------------
# Non-interactive mode — core scenarios
# ---------------------------------------------------------------------------


class TestInitNonInteractive:
    def test_creates_pfactory_yml_with_one_http_target(self, tmp_path: Path) -> None:
        result = run_init(
            tmp_path,
            "--non-interactive",
            "--target-name", "api",
            "--target-type", "http",
            "--base-url", "https://api.staging.example.com",
        )
        assert result.exit_code == 0, result.output
        yml_path = tmp_path / ".pfactory.yml"
        assert yml_path.exists(), ".pfactory.yml not created"
        # Parse rather than substring-match the YAML text: "https://..." loose in
        # the file proves nothing about which key it landed under (the generated
        # header even carries an unrelated docs URL).
        config = yaml.safe_load(yml_path.read_text())
        assert config["version"] == 1
        assert [t["name"] for t in config["targets"]] == ["api"]
        assert config["targets"][0]["base_url"] == "https://api.staging.example.com"

    def test_creates_pfactory_yml_with_bearer_auth(self, tmp_path: Path) -> None:
        result = run_init(
            tmp_path,
            "--non-interactive",
            "--target-name", "web",
            "--target-type", "http",
            "--base-url", "https://staging.example.com",
            "--auth-type", "bearer",
            "--auth-token-env", "STAGING_TOKEN",
        )
        assert result.exit_code == 0, result.output
        text = (tmp_path / ".pfactory.yml").read_text()
        assert "bearer" in text
        assert "STAGING_TOKEN" in text

    def test_creates_pfactory_yml_with_basic_auth(self, tmp_path: Path) -> None:
        result = run_init(
            tmp_path,
            "--non-interactive",
            "--target-name", "web",
            "--target-type", "http",
            "--base-url", "https://staging.example.com",
            "--auth-type", "basic",
            "--auth-username-env", "API_USER",
            "--auth-password-env", "API_PASS",
        )
        assert result.exit_code == 0, result.output
        text = (tmp_path / ".pfactory.yml").read_text()
        assert "basic" in text
        assert "API_USER" in text
        assert "API_PASS" in text

    def test_creates_empty_tests_catalog(self, tmp_path: Path) -> None:
        run_init(
            tmp_path,
            "--non-interactive",
            "--target-name", "api",
            "--target-type", "http",
            "--base-url", "https://api.example.com",
        )
        catalog_path = tmp_path / ".pfactory" / "tests-catalog.json"
        assert catalog_path.exists(), "tests-catalog.json not created"
        data = json.loads(catalog_path.read_text())
        assert data["version"] == 1
        assert data["tests"] == []
        assert "updated_at" in data

    def test_refuses_to_overwrite_without_force(self, tmp_path: Path) -> None:
        existing = tmp_path / ".pfactory.yml"
        existing.write_text("# existing\nversion: 1\ntargets: []\n")
        runner = CliRunner()
        result = runner.invoke(
            pfactory_main,
            ["init", "--repo-root", str(tmp_path),
             "--non-interactive", "--target-name", "api",
             "--target-type", "http", "--base-url", "https://api.example.com"],
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        # File should remain unchanged
        assert "existing" in existing.read_text()

    def test_force_overwrites(self, tmp_path: Path) -> None:
        existing = tmp_path / ".pfactory.yml"
        existing.write_text("# old\nversion: 1\n")
        result = run_init(
            tmp_path,
            "--force",
            "--non-interactive",
            "--target-name", "new-api",
            "--target-type", "http",
            "--base-url", "https://new.example.com",
        )
        assert result.exit_code == 0, result.output
        config = yaml.safe_load(existing.read_text())
        assert [t["name"] for t in config["targets"]] == ["new-api"]
        assert config["targets"][0]["base_url"] == "https://new.example.com"

    def test_validates_generated_yaml_via_load_pfactory_yml(self, tmp_path: Path) -> None:
        """Validate that the generated YAML is parseable by load_pfactory_yml."""
        run_init(
            tmp_path,
            "--non-interactive",
            "--target-name", "api",
            "--target-type", "http",
            "--base-url", "https://api.staging.example.com",
        )
        from pfactory_yml import load_pfactory_yml

        config = load_pfactory_yml(tmp_path)
        assert config is not None, "load_pfactory_yml returned None"
        assert len(config.targets) == 1
        target = config.targets[0]
        assert target.name == "api"
        assert target.type == "http"

    def test_init_error_missing_target_name(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            pfactory_main,
            ["init", "--repo-root", str(tmp_path),
             "--non-interactive",
             "--target-type", "http",
             "--base-url", "https://api.example.com"],
            catch_exceptions=False,
        )
        assert result.exit_code != 0

    def test_init_error_missing_base_url_for_http(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            pfactory_main,
            ["init", "--repo-root", str(tmp_path),
             "--non-interactive",
             "--target-name", "api",
             "--target-type", "http"],
            catch_exceptions=False,
        )
        assert result.exit_code != 0

    def test_init_does_not_overwrite_existing_catalog(self, tmp_path: Path) -> None:
        """Existing catalog is preserved — init does not clobber it."""
        catalog_path = tmp_path / ".pfactory" / "tests-catalog.json"
        catalog_path.parent.mkdir(parents=True)
        original = {"version": 1, "updated_at": "2026-01-01T00:00:00Z", "tests": [{"id": "x"}]}
        catalog_path.write_text(json.dumps(original))

        run_init(
            tmp_path,
            "--non-interactive",
            "--target-name", "api",
            "--target-type", "http",
            "--base-url", "https://api.example.com",
        )
        # Catalog should remain unchanged
        data = json.loads(catalog_path.read_text())
        assert data == original


# ---------------------------------------------------------------------------
# docker_compose target
# ---------------------------------------------------------------------------


class TestInitDockerCompose:
    def test_creates_docker_compose_target(self, tmp_path: Path) -> None:
        result = run_init(
            tmp_path,
            "--non-interactive",
            "--target-name", "web",
            "--target-type", "docker_compose",
            "--compose-file", "docker-compose.test.yml",
            "--compose-services", "app,db",
        )
        assert result.exit_code == 0, result.output
        text = (tmp_path / ".pfactory.yml").read_text()
        assert "docker_compose" in text
        assert "app" in text
