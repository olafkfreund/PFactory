"""The pre-commit hook must find Python tooling from a git worktree (#796).

A worktree has no ``apps/backend/.venv`` of its own — it is gitignored and
lives only in the checkout that created it. The hook used to fall through to a
bare ``python`` with no pytest and block the commit with "Python tests failed",
naming the wrong cause. ``scripts/resolve_backend_venv.sh`` resolves the main
checkout's venv instead.

These tests run the shipped script, in a real ``git worktree``, rather than
asserting on the hook's text: a text assertion passes for any resolver that
merely mentions ``--git-common-dir``, including a broken one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "resolve_backend_venv.sh"
_PROBE = f'. "{_SCRIPT}"; echo "rc=$?"; echo "venv=$BACKEND_VENV"'


def _run(cwd: Path) -> str:
    """Source the resolver with ``cwd`` as the working directory."""
    return subprocess.run(
        ["sh", "-c", _PROBE],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def test_worktree_falls_back_to_the_main_checkout_venv(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "--detach", str(worktree), "HEAD"],
        cwd=_REPO,
        check=True,
        capture_output=True,
    )
    try:
        assert not (worktree / "apps" / "backend" / ".venv").exists()
        out = _run(worktree)
        assert f"venv={_REPO}/apps/backend/.venv" in out
        assert "rc=0" in out
        # The cross-tree resolution is announced: a worktree on a branch with
        # different dependencies would otherwise be tested silently against the
        # main checkout's.
        assert "Using the main checkout's venv" in out
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=_REPO,
            check=False,
            capture_output=True,
        )


def test_main_checkout_resolves_its_own_venv_without_announcing() -> None:
    out = _run(_REPO)
    assert f"venv={_REPO}/apps/backend/.venv" in out
    assert "rc=0" in out
    assert "Using the main checkout's venv" not in out


def test_no_venv_anywhere_returns_empty_and_nonzero(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    out = _run(tmp_path)
    assert "venv=" in out
    assert "venv=/" not in out  # empty, not a path
    assert "rc=1" in out


def test_the_hook_uses_the_resolver() -> None:
    hook = (_REPO / ".husky" / "pre-commit").read_text()
    assert "resolve_backend_venv.sh" in hook
    # The hardcoded prefixes the resolver replaced must be gone from the
    # command lines (the remaining mentions are a comment and a message).
    assert "apps/backend/.venv/bin/pytest" not in hook
    assert "apps/backend/.venv/bin/ruff" not in hook
