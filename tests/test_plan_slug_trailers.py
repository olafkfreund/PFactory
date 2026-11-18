"""Exercise trailer extraction against real, isolated git histories."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/plan_slug_trailers.sh"
GIT = shutil.which("git")


def git(repo, *args):
    return subprocess.run(  # noqa: S603 - fixed git executable and test-owned arguments
        [GIT, *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "commit.gpgsign", "false")
    commit(tmp_path)
    return tmp_path


def commit(repo, *slugs):
    message = "Test commit\n\n" + "\n".join(f"Plan-Slug: {slug}" for slug in slugs)
    git(repo, "commit", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def extract(repo, *args):
    result = subprocess.run(  # noqa: S603 - repository script with test-owned arguments
        [str(SCRIPT), *args], cwd=repo, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert result.stderr == ""
    return result.stdout


def test_head_trailer(repo):
    commit(repo, "older")
    commit(repo, "2026-11-18-1958-bump")
    assert extract(repo) == "Plan-Slug: 2026-11-18-1958-bump\n"


def test_no_trailer(repo):
    assert extract(repo) == ""


@pytest.mark.parametrize("slug", ["bad slug", "bad$slug", "a" * 129, "é", ""])
def test_invalid_slug(repo, slug):
    commit(repo, slug)
    assert extract(repo) == ""


@pytest.mark.parametrize("slug", ["a", "a" * 128, "Az09._/#:-"])
def test_valid_slug(repo, slug):
    commit(repo, slug)
    assert extract(repo) == f"Plan-Slug: {slug}\n"


def test_range_deduplicates_newest_first(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "old")
    commit(repo, "new")
    commit(repo, "old")
    assert extract(repo, base) == "Plan-Slug: old\nPlan-Slug: new\n"


def test_missing_base_falls_back_to_head(repo):
    commit(repo, "older")
    commit(repo, "head")
    assert extract(repo, "deadbeef") == "Plan-Slug: head\n"


def test_non_git_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert extract(tmp_path) == ""


def test_multiple_trailers(repo):
    commit(repo, "first", "second")
    assert extract(repo) == "Plan-Slug: first\nPlan-Slug: second\n"


def test_cap_at_ten(repo):
    base = git(repo, "rev-parse", "HEAD")
    for index in range(12):
        commit(repo, f"slug-{index}")
    assert extract(repo, base) == "".join(f"Plan-Slug: slug-{i}\n" for i in range(11, 1, -1))
