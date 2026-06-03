"""Tests for the AIFactory→PFactory spec snapshotter — Task 3 (#4) + Task 4 (#20).

Sub-task 3.1 snapshotter side (original):
  - happy path copies spec.md + implementation_plan.json
  - copies are read-only (0o444)
  - source.json contains expected metadata
  - missing source spec dir → SnapshotError
  - missing spec.md → result.has_spec_md = False + warning, no exception
  - missing implementation_plan.json → result.has_plan_json = False + warning
  - project_root_path=None → no diff captured, warning recorded
  - real git repo → diff captured, sha recorded
  - bad git repo → soft fail with warning, no exception

Sub-task 4.4 new cases (Task 4 / #20):
  - .pfactory.yml present → context/pfactory_yml.json written, has_pfactory_yml=True
  - .pfactory.yml absent → flag False, no file, no warning
  - .pfactory.yml malformed → flag False, no file, warning recorded
  - tests-catalog present → context/tests_catalog.json written, has_tests_catalog=True
  - tests-catalog absent → flag False, no file, no warning
  - tests-catalog malformed → flag False, no file, warning recorded
  - both files present → both flags True, both files written
  - project_root_path=None → both flags False, no extra warnings
  - source.json carries has_pfactory_yml + has_tests_catalog keys
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from workspaces import SnapshotError, snapshot_aifactory_spec

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def aifactory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated AIFactory root for one test."""
    root = tmp_path / "aifactory"
    monkeypatch.setenv("PFACTORY_AIFACTORY_ROOT", str(root))
    return root


@pytest.fixture
def populated_spec(aifactory: Path) -> Path:
    """An AIFactory spec dir with spec.md + implementation_plan.json."""
    spec = aifactory / "workspaces" / "demo" / "specs" / "001-login"
    spec.mkdir(parents=True)
    (spec / "spec.md").write_text("# Login spec\n\nAccept on bcrypt success.\n")
    (spec / "implementation_plan.json").write_text(
        '{"phases": [{"id": "p1", "name": "auth"}]}'
    )
    return spec


# ── Happy path ───────────────────────────────────────────────────────────


def test_snapshot_happy_path(populated_spec: Path, tmp_path: Path) -> None:
    dest = tmp_path / "pfactory" / "workspaces" / "demo" / "specs" / "001-login"
    res = snapshot_aifactory_spec(
        project_id="demo",
        spec_id="001-login",
        branch="feature/login",
        base_ref="main",
        project_root_path=None,  # skip git
        dest_spec_dir=dest,
    )
    assert res.has_spec_md is True
    assert res.has_plan_json is True
    assert res.aifactory_spec_dir == str(populated_spec)
    assert (dest / "context" / "aifactory_spec.md").exists()
    assert (dest / "context" / "aifactory_plan.json").exists()
    assert (dest / "context" / "source.json").exists()


def test_copies_are_read_only(populated_spec: Path, tmp_path: Path) -> None:
    dest = tmp_path / "pfactory" / "workspaces" / "demo" / "specs" / "001-login"
    snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/login", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    for fname in ("aifactory_spec.md", "aifactory_plan.json"):
        mode = (dest / "context" / fname).stat().st_mode & 0o777
        assert mode == 0o444, f"{fname} mode is {oct(mode)}, expected 0o444"


def test_source_json_schema(populated_spec: Path, tmp_path: Path) -> None:
    dest = tmp_path / "pfactory" / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/x", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    data = json.loads((dest / "context" / "source.json").read_text())
    expected_keys = {
        "project_id", "spec_id", "branch", "base_ref",
        "aifactory_spec_dir", "snapshotted_at",
        "has_spec_md", "has_plan_json", "has_diff_patch",
        "sha_at_handover", "diff_stat", "warnings",
    }
    assert expected_keys.issubset(data.keys())
    assert data["project_id"] == "demo"
    assert data["spec_id"] == "001-login"
    assert data["branch"] == "feature/x"
    assert data["base_ref"] == "main"


def test_source_json_carries_handback_target(
    populated_spec: Path, tmp_path: Path
) -> None:
    """P1 (#183): source.json carries the AIFactory hand-back envelope so a
    correction can target the original spec (epic #182)."""
    dest = tmp_path / "pfactory" / "ws"
    snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/x", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    data = json.loads((dest / "context" / "source.json").read_text())

    # correction_cycle starts at zero (bounds the test→fix→re-test loop).
    assert data["correction_cycle"] == 0

    # The handback builder (P2) reads source["aifactory"] as one unit.
    af = data["aifactory"]
    assert af["project_id"] == "demo"
    assert af["spec_id"] == "001-login"
    assert af["task_id"] == "demo:001-login"
    assert af["api_url"] == "http://localhost:3101"  # AIFactory web-server default
    # The flat field is folded into the envelope, not duplicated at top level.
    assert "aifactory_api_url" not in data


def test_handback_api_url_env_override(
    populated_spec: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PFACTORY_AIFACTORY_API_URL overrides the default (remote AIFactory)."""
    monkeypatch.setenv("PFACTORY_AIFACTORY_API_URL", "https://aif.internal:8443")
    dest = tmp_path / "pfactory" / "ws"
    snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/x", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    data = json.loads((dest / "context" / "source.json").read_text())
    assert data["aifactory"]["api_url"] == "https://aif.internal:8443"


def test_handback_api_url_explicit_arg_wins(
    populated_spec: Path, tmp_path: Path
) -> None:
    """An explicit api_url arg beats the env default."""
    dest = tmp_path / "pfactory" / "ws"
    snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/x", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
        api_url="http://example:9000",
    )
    data = json.loads((dest / "context" / "source.json").read_text())
    assert data["aifactory"]["api_url"] == "http://example:9000"


# ── Soft-fail paths (warnings, not exceptions) ───────────────────────────


def test_missing_spec_md_is_soft_fail(aifactory: Path, tmp_path: Path) -> None:
    """An AIFactory dir with only the JSON file is still snapshottable."""
    spec = aifactory / "workspaces" / "demo" / "specs" / "001"
    spec.mkdir(parents=True)
    (spec / "implementation_plan.json").write_text('{"phases": []}')
    dest = tmp_path / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001",
        branch="f/x", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    assert res.has_spec_md is False
    assert res.has_plan_json is True
    assert any("spec.md missing" in w for w in res.warnings)


def test_missing_plan_json_is_soft_fail(aifactory: Path, tmp_path: Path) -> None:
    spec = aifactory / "workspaces" / "demo" / "specs" / "001"
    spec.mkdir(parents=True)
    (spec / "spec.md").write_text("# spec")
    dest = tmp_path / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001",
        branch="f/x", base_ref="main",
        project_root_path=None, dest_spec_dir=dest,
    )
    assert res.has_spec_md is True
    assert res.has_plan_json is False
    assert any("implementation_plan.json missing" in w for w in res.warnings)


def test_project_root_none_skips_git_with_warning(populated_spec: Path, tmp_path: Path) -> None:
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/login", base_ref="main",
        project_root_path=None, dest_spec_dir=tmp_path / "ws",
    )
    assert res.has_diff_patch is False
    assert any("project_root_path not provided" in w for w in res.warnings)


def test_bad_project_root_is_soft_fail(populated_spec: Path, tmp_path: Path) -> None:
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/login", base_ref="main",
        project_root_path=tmp_path / "does" / "not" / "exist",
        dest_spec_dir=tmp_path / "ws",
    )
    assert res.has_diff_patch is False
    assert any("not a directory" in w for w in res.warnings)


# ── Hard fail: missing source spec dir ───────────────────────────────────


def test_missing_source_spec_dir_raises(aifactory: Path, tmp_path: Path) -> None:
    with pytest.raises(SnapshotError) as exc:
        snapshot_aifactory_spec(
            project_id="ghost", spec_id="404",
            branch="f/x", base_ref="main",
            project_root_path=None, dest_spec_dir=tmp_path / "ws",
        )
    assert "AIFactory spec dir not found" in str(exc.value)


# ── Real-git happy path ──────────────────────────────────────────────────


def _git(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *cmd],
        capture_output=True, text=True, check=True
    ).stdout.strip()


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _git_available(), reason="git not on PATH")
def test_git_diff_captured_for_real_repo(populated_spec: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "tester"], repo)

    (repo / "a.py").write_text("def add(a, b):\n    return a + b\n")
    _git(["add", "a.py"], repo)
    _git(["commit", "-m", "base"], repo)

    _git(["checkout", "-b", "feature/login"], repo)
    (repo / "a.py").write_text("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
    _git(["add", "a.py"], repo)
    _git(["commit", "-m", "add sub"], repo)

    dest = tmp_path / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo", spec_id="001-login",
        branch="feature/login", base_ref="main",
        project_root_path=repo, dest_spec_dir=dest,
    )

    assert res.has_diff_patch is True
    diff_text = (dest / "context" / "diff.patch").read_text()
    assert "def sub" in diff_text  # the added function
    assert res.sha_at_handover is not None
    assert len(res.sha_at_handover) == 40  # full sha
    assert res.diff_stat is not None


# ── Task 4 (#20): .pfactory.yml + tests-catalog capture ──────────────────

# Minimal valid fixture content (per task spec)
_PFACTORY_YML = """\
version: 1
targets:
  - name: api
    type: http
    base_url: https://api.example.com
"""

_TESTS_CATALOG_JSON = """\
{
  "version": 1,
  "updated_at": "2026-05-28T00:00:00Z",
  "tests": []
}
"""


def _make_project_root(tmp_path: Path) -> Path:
    """Return a bare project root directory (no .pfactory.yml or catalog)."""
    root = tmp_path / "project_root"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _add_pfactory_yml(project_root: Path) -> None:
    """Write a minimal valid .pfactory.yml into *project_root*."""
    (project_root / ".pfactory.yml").write_text(_PFACTORY_YML)


def _add_tests_catalog(project_root: Path) -> None:
    """Write a minimal valid tests-catalog.json into *project_root*."""
    catalog_dir = project_root / ".pfactory"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / "tests-catalog.json").write_text(_TESTS_CATALOG_JSON)


def _call_snapshot(
    populated_spec: Path,
    tmp_path: Path,
    project_root: Path | None,
) -> tuple[object, Path]:
    """Run snapshot_aifactory_spec and return (result, dest_context_dir)."""
    dest = tmp_path / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo",
        spec_id="001-login",
        branch="feature/login",
        base_ref="main",
        project_root_path=project_root,
        dest_spec_dir=dest,
    )
    return res, dest / "context"


# ── .pfactory.yml cases ──────────────────────────────────────────────────


def test_snapshot_with_pfactory_yml_writes_context_file(
    populated_spec: Path, tmp_path: Path
) -> None:
    """A project_root containing .pfactory.yml → context/pfactory_yml.json
    is written, has_pfactory_yml=True, and the JSON parses with targets."""
    project_root = _make_project_root(tmp_path)
    _add_pfactory_yml(project_root)

    res, ctx = _call_snapshot(populated_spec, tmp_path, project_root)

    assert res.has_pfactory_yml is True
    yml_file = ctx / "pfactory_yml.json"
    assert yml_file.exists(), "context/pfactory_yml.json must be written"
    data = json.loads(yml_file.read_text())
    # The parsed JSON should have a 'targets' list with our 'api' target
    assert "targets" in data
    target_names = [t.get("name") for t in data["targets"]]
    assert "api" in target_names
    # File must be read-only
    mode = yml_file.stat().st_mode & 0o777
    assert mode == 0o444, f"Expected 0o444, got {oct(mode)}"


def test_snapshot_without_pfactory_yml(
    populated_spec: Path, tmp_path: Path
) -> None:
    """No .pfactory.yml in project_root → has_pfactory_yml=False, no
    context/pfactory_yml.json, no warning about absence."""
    project_root = _make_project_root(tmp_path)  # no .pfactory.yml

    res, ctx = _call_snapshot(populated_spec, tmp_path, project_root)

    assert res.has_pfactory_yml is False
    assert not (ctx / "pfactory_yml.json").exists()
    # Absence must not emit a warning
    assert not any("pfactory.yml" in w for w in res.warnings)


def test_snapshot_with_unparseable_pfactory_yml_records_warning(
    populated_spec: Path, tmp_path: Path
) -> None:
    """A malformed .pfactory.yml → flag stays False, warning recorded,
    no context/pfactory_yml.json written, no exception raised."""
    project_root = _make_project_root(tmp_path)
    # Write invalid YAML (Pydantic will reject 'type: unknown')
    (project_root / ".pfactory.yml").write_text(
        "version: 1\ntargets:\n  - name: bad\n    type: unknown_type\n"
    )

    res, ctx = _call_snapshot(populated_spec, tmp_path, project_root)

    assert res.has_pfactory_yml is False
    assert not (ctx / "pfactory_yml.json").exists()
    assert any("unparseable" in w for w in res.warnings), (
        f"Expected 'unparseable' warning, got: {res.warnings}"
    )


# ── tests-catalog cases ──────────────────────────────────────────────────


def test_snapshot_with_tests_catalog_writes_context_file(
    populated_spec: Path, tmp_path: Path
) -> None:
    """A project_root containing .pfactory/tests-catalog.json →
    context/tests_catalog.json is written, has_tests_catalog=True,
    and the JSON parses correctly."""
    project_root = _make_project_root(tmp_path)
    _add_tests_catalog(project_root)

    res, ctx = _call_snapshot(populated_spec, tmp_path, project_root)

    assert res.has_tests_catalog is True
    cat_file = ctx / "tests_catalog.json"
    assert cat_file.exists(), "context/tests_catalog.json must be written"
    data = json.loads(cat_file.read_text())
    assert "version" in data
    assert data["version"] == 1
    assert "tests" in data
    # File must be read-only
    mode = cat_file.stat().st_mode & 0o777
    assert mode == 0o444, f"Expected 0o444, got {oct(mode)}"


def test_snapshot_without_tests_catalog(
    populated_spec: Path, tmp_path: Path
) -> None:
    """No .pfactory/tests-catalog.json → has_tests_catalog=False, no
    context/tests_catalog.json, no warning about absence."""
    project_root = _make_project_root(tmp_path)  # no catalog

    res, ctx = _call_snapshot(populated_spec, tmp_path, project_root)

    assert res.has_tests_catalog is False
    assert not (ctx / "tests_catalog.json").exists()
    assert not any("tests-catalog" in w for w in res.warnings)


def test_snapshot_with_unparseable_tests_catalog_records_warning(
    populated_spec: Path, tmp_path: Path
) -> None:
    """A malformed tests-catalog.json → flag stays False, warning recorded,
    no context/tests_catalog.json written, no exception raised."""
    project_root = _make_project_root(tmp_path)
    catalog_dir = project_root / ".pfactory"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    # Write invalid JSON
    (catalog_dir / "tests-catalog.json").write_text(
        '{"version": 1, "updated_at": "bad", "tests": [{"lane": "INVALID_LANE"}]}'
    )

    res, ctx = _call_snapshot(populated_spec, tmp_path, project_root)

    assert res.has_tests_catalog is False
    assert not (ctx / "tests_catalog.json").exists()
    assert any("unparseable" in w for w in res.warnings), (
        f"Expected 'unparseable' warning, got: {res.warnings}"
    )


# ── Combined + edge cases ────────────────────────────────────────────────


def test_snapshot_with_both_yaml_and_catalog(
    populated_spec: Path, tmp_path: Path
) -> None:
    """Both .pfactory.yml and tests-catalog.json present → both flags True,
    both context files written."""
    project_root = _make_project_root(tmp_path)
    _add_pfactory_yml(project_root)
    _add_tests_catalog(project_root)

    res, ctx = _call_snapshot(populated_spec, tmp_path, project_root)

    assert res.has_pfactory_yml is True
    assert res.has_tests_catalog is True
    assert (ctx / "pfactory_yml.json").exists()
    assert (ctx / "tests_catalog.json").exists()


def test_snapshot_no_project_root_path_skips_both(
    populated_spec: Path, tmp_path: Path
) -> None:
    """project_root_path=None → both flags False, no yml/catalog warning
    (only the expected 'not provided' git warning)."""
    dest = tmp_path / "ws"
    res = snapshot_aifactory_spec(
        project_id="demo",
        spec_id="001-login",
        branch="feature/login",
        base_ref="main",
        project_root_path=None,
        dest_spec_dir=dest,
    )

    assert res.has_pfactory_yml is False
    assert res.has_tests_catalog is False
    ctx = dest / "context"
    assert not (ctx / "pfactory_yml.json").exists()
    assert not (ctx / "tests_catalog.json").exists()
    # The git skip warning IS expected; no yml/catalog-specific warnings
    assert not any("pfactory.yml" in w for w in res.warnings)
    assert not any("tests-catalog" in w for w in res.warnings)


def test_source_json_includes_new_flags(
    populated_spec: Path, tmp_path: Path
) -> None:
    """source.json written to context/ contains has_pfactory_yml and
    has_tests_catalog keys with correct bool values."""
    project_root = _make_project_root(tmp_path)
    _add_pfactory_yml(project_root)
    _add_tests_catalog(project_root)

    dest = tmp_path / "ws"
    snapshot_aifactory_spec(
        project_id="demo",
        spec_id="001-login",
        branch="feature/login",
        base_ref="main",
        project_root_path=project_root,
        dest_spec_dir=dest,
    )

    data = json.loads((dest / "context" / "source.json").read_text())
    assert "has_pfactory_yml" in data, "source.json must include has_pfactory_yml"
    assert "has_tests_catalog" in data, "source.json must include has_tests_catalog"
    assert data["has_pfactory_yml"] is True
    assert data["has_tests_catalog"] is True
