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
# The throwaway repo carries no mypy config; gate it with the real strict one.
MYPY_CONFIG = REPO_ROOT / "standards" / "mypy.ini"


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


def _ratchet(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = _clean_env()
    ruff_dir = _ruff_dir()
    assert ruff_dir is not None
    env["PATH"] = f"{ruff_dir}{os.pathsep}{env.get('PATH', '')}"
    # Test-controlled argv (our own interpreter + in-repo script).
    return subprocess.run(  # noqa: S603
        [sys.executable, str(RATCHET), "--staged", "--package", "apps/backend", *extra],
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


def _mypy_dir() -> str | None:
    """Directory holding a mypy binary (venv first, then PATH), or None."""
    venv_mypy = Path(sys.executable).parent / "mypy"
    if venv_mypy.exists():
        return str(venv_mypy.parent)
    on_path = shutil.which("mypy")
    return str(Path(on_path).parent) if on_path else None


def test_a_staged_pass_says_which_half_ran(repo: Path) -> None:
    """A ruff-only pass must not read like a full pass (#786).

    The line used to say "no changed file regressed" whether or not mypy had
    run, so a hook whose mypy half is disabled by design looked identical to
    CI's stricter gate — which is how two mypy regressions reached CI unseen.
    """
    legacy = repo / "apps" / "backend" / "legacy.py"
    legacy.write_text(legacy.read_text() + "# harmless edit\n")
    _git(repo, "add", "-A")
    res = _ratchet(repo)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ratchet PASSED" in res.stdout
    assert "(ruff only; mypy runs in CI)" in res.stdout


def test_mypy_and_no_mypy_together_are_rejected(repo: Path) -> None:
    res = _ratchet(repo, "--mypy", "--no-mypy")
    assert res.returncode == 2, res.stdout + res.stderr
    assert "not allowed with argument" in res.stderr


@pytest.mark.skipif(_mypy_dir() is None, reason="mypy not available")
def test_opt_in_mypy_blocks_a_net_new_type_error(repo: Path) -> None:
    """The #778 / #785 case, caught locally instead of in CI (#786).

    Staged mode is ruff-only by default, so the same commit passes without
    `--mypy` — that is the documented trade, and asserting both halves keeps
    the opt-in honest about what it buys.
    """
    typed = repo / "apps" / "backend" / "typed.py"
    # Clean under ruff's T20-only config here, but a `mypy --strict` error.
    typed.write_text("def f(x: int) -> str:\n    return x\n")
    _git(repo, "add", "-A")

    default_run = _ratchet(repo)
    assert default_run.returncode == 0, default_run.stdout + default_run.stderr

    env_path = f"{_mypy_dir()}{os.pathsep}{os.environ.get('PATH', '')}"
    opted_in = _ratchet(repo, "--mypy", "--mypy-config", str(MYPY_CONFIG))
    assert opted_in.returncode == 1, (
        f"--mypy did not block a net-new type error (PATH={env_path})\n"
        + opted_in.stdout
        + opted_in.stderr
    )
    assert "mypy" in opted_in.stdout.lower()
