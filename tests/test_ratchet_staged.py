"""Staged (pre-commit) mode of scripts/ratchet_lint.py (issue #389).

The .husky/pre-commit hook gates the git INDEX with the same per-file
no-regression rule CI uses: pre-existing debt in a touched file must not
block a commit; a net-new violation must. These tests drive the ratchet as a
subprocess against a throwaway git repo, exactly as the hook invokes it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RATCHET = REPO_ROOT / "scripts" / "ratchet_lint.py"


def _ruff_dir() -> str | None:
    """Directory holding a ruff binary (venv first, then PATH), or None."""
    venv_ruff = Path(sys.executable).parent / "ruff"
    if venv_ruff.exists():
        return str(venv_ruff.parent)
    on_path = shutil.which("ruff")
    return str(Path(on_path).parent) if on_path else None


pytestmark = pytest.mark.skipif(_ruff_dir() is None, reason="ruff not available")


def _clean_env() -> dict[str, str]:
    """os.environ without git's per-repo variables.

    When these tests run from inside a git hook (the pre-commit hook runs
    pytest), git exports GIT_DIR / GIT_INDEX_FILE etc. pointing at the REAL
    repo; a child git process in a tmp repo inheriting them would operate on
    (and corrupt) the real index instead of the tmp one.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args: str) -> None:
    # Test-controlled argv against a throwaway repo; git resolved from PATH.
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        env=_clean_env(),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo whose base commit already carries one T201 violation."""
    (tmp_path / "ruff.toml").write_text('[lint]\nselect = ["T20"]\n')
    pkg = tmp_path / "apps" / "backend"
    pkg.mkdir(parents=True)
    (pkg / "legacy.py").write_text('print("pre-existing debt")\n')
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _ratchet(repo: Path) -> subprocess.CompletedProcess[str]:
    env = _clean_env()
    ruff_dir = _ruff_dir()
    assert ruff_dir is not None
    env["PATH"] = f"{ruff_dir}{os.pathsep}{env.get('PATH', '')}"
    # Test-controlled argv (our own interpreter + in-repo script).
    return subprocess.run(  # noqa: S603
        [sys.executable, str(RATCHET), "--staged", "--package", "apps/backend"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_pre_existing_debt_does_not_block(repo: Path) -> None:
    legacy = repo / "apps" / "backend" / "legacy.py"
    legacy.write_text(legacy.read_text() + "# harmless edit\n")
    _git(repo, "add", "-A")
    res = _ratchet(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ratchet PASSED" in res.stdout


def test_net_new_violation_blocks(repo: Path) -> None:
    legacy = repo / "apps" / "backend" / "legacy.py"
    legacy.write_text(legacy.read_text() + 'print("net-new violation")\n')
    _git(repo, "add", "-A")
    res = _ratchet(repo)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "T201" in res.stdout


def test_gates_the_index_not_the_worktree(repo: Path) -> None:
    legacy = repo / "apps" / "backend" / "legacy.py"
    legacy.write_text(legacy.read_text() + "# harmless edit\n")
    _git(repo, "add", "-A")
    # A violation that exists only in the worktree must not gate the commit.
    legacy.write_text(legacy.read_text() + 'print("unstaged")\n')
    res = _ratchet(repo)
    assert res.returncode == 0, res.stdout + res.stderr
