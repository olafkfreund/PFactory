"""The pre-commit hook must not rewrite files with an unpinned ruff (#452).

`.husky/pre-commit` is the hook that actually runs -- `core.hooksPath` points at
`.husky/`, so `.pre-commit-config.yaml` (whose ruff rev IS pinned, and IS
agreement-tested in test_ruff_pin_agreement.py) is dormant unless someone invokes
`pre-commit run` by hand. The live hook resolved whatever ruff sat in
`apps/backend/.venv/bin` or on PATH, with no version check at all.

That matters because the hook does not merely lint. It runs `ruff check --fix`,
then `ruff format`, then `git add`s the result. A version skew therefore does not
produce a warning, it produces a commit: with a local ruff 0.14.10, formatting
`apps/backend/plan/service.py` collapsed eleven import blocks that the pinned
0.15.17 keeps exploded, staged the 44-line unrelated diff automatically, and
would have reddened the blocking CI format check on a file the author never
meant to reformat.

These tests run the REAL hook against a stub `ruff` of a chosen version and
assert on whether the staged file came back rewritten, rather than grepping the
script for a guard that might not be wired to anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_HOOK = _REPO / ".husky" / "pre-commit"

_PINNED = "0.15.17"
_SKEWED = "0.14.10"

# What the stub ruff appends when it is asked to format. Its presence in the
# staged file is the evidence that the hook rewrote the developer's work.
_REWRITE_MARKER = "# rewritten-by-stub-ruff\n"

_STUB_RUFF = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "ruff {version}"
  exit 0
fi
# Any non-version invocation is a rewrite attempt: mark every .py argument.
for arg in "$@"; do
  case "$arg" in
    *.py) printf '%s' '{marker}' >> "$arg" ;;
  esac
done
exit 0
"""

_CI_YML = """name: ci
env:
  RUFF_VERSION: "{pinned}"
jobs:
  backend:
    runs-on: ubuntu-latest
"""

_SOURCE = "x = 1\n"


def _sandbox(tmp_path: Path, stub_version: str) -> Path:
    """A throwaway repo with the real hook, a stub ruff, and one staged .py."""
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text(
        _CI_YML.format(pinned=_PINNED), encoding="utf-8"
    )

    # apps/web-server, not apps/backend: the hook's ruff block covers both, but
    # only apps/backend triggers the (slow) pytest section further down.
    target = repo / "apps" / "web-server" / "probe.py"
    target.parent.mkdir(parents=True)
    target.write_text(_SOURCE, encoding="utf-8")

    hook = repo / "pre-commit"
    shutil.copy(_HOOK, hook)
    hook.chmod(0o755)

    bindir = repo / "stub-bin"
    bindir.mkdir()
    stub = bindir / "ruff"
    stub.write_text(
        _STUB_RUFF.format(version=stub_version, marker=_REWRITE_MARKER), encoding="utf-8"
    )
    stub.chmod(0o755)

    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "apps/web-server/probe.py"],
    ):
        # Test-controlled argv against a throwaway repo; git resolved from PATH.
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)  # noqa: S603
    return repo


def _run_hook(repo: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{repo / 'stub-bin'}{os.pathsep}{env['PATH']}"
    # The hook's own comment explains why these must not leak into a child git.
    for leaked in (
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_WORK_TREE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_CONFIG_PARAMETERS",
    ):
        env.pop(leaked, None)
    # Test-controlled argv against a throwaway repo; sh resolved from PATH.
    # check=False: a skewed ruff makes the ratchet step below fail, which is
    # irrelevant here -- the assertion is about the file, not the exit code.
    return subprocess.run(
        ["sh", "./pre-commit"],  # noqa: S607
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.skipif(not _HOOK.is_file(), reason=".husky/pre-commit not present")
def test_a_skewed_ruff_does_not_rewrite_staged_files(tmp_path: Path) -> None:
    repo = _sandbox(tmp_path, _SKEWED)
    out = _run_hook(repo)
    body = (repo / "apps" / "web-server" / "probe.py").read_text(encoding="utf-8")

    assert _REWRITE_MARKER not in body, (
        "the hook reformatted a staged file with ruff "
        f"{_SKEWED} while CI pins {_PINNED}. That is a commit, not a warning."
    )
    assert body == _SOURCE
    combined = out.stdout + out.stderr
    assert "ruff version skew" in combined, f"no diagnostic explaining the skip:\n{combined}"
    assert _PINNED in combined, "the message must name the version to install"


@pytest.mark.skipif(not _HOOK.is_file(), reason=".husky/pre-commit not present")
def test_the_pinned_ruff_still_rewrites(tmp_path: Path) -> None:
    """The guard must gate on the VERSION, not disable the hook's whole job."""
    repo = _sandbox(tmp_path, _PINNED)
    out = _run_hook(repo)
    body = (repo / "apps" / "web-server" / "probe.py").read_text(encoding="utf-8")

    assert _REWRITE_MARKER in body, (
        "with the pinned ruff the hook must still autofix + format + re-stage; "
        f"it did not:\n{out.stdout}\n{out.stderr}"
    )
    assert "ruff version skew" not in (out.stdout + out.stderr)


@pytest.mark.skipif(not _HOOK.is_file(), reason=".husky/pre-commit not present")
def test_an_unreadable_pin_is_treated_as_a_skew(tmp_path: Path) -> None:
    """Rule 4.7: a comparison that cannot be made has not been made.

    If ci.yml is renamed or stops declaring RUFF_VERSION, the hook cannot know
    whether the local ruff is the right one -- so it must not rewrite, rather
    than assume.
    """
    repo = _sandbox(tmp_path, _PINNED)
    (repo / ".github" / "workflows" / "ci.yml").unlink()
    _run_hook(repo)
    body = (repo / "apps" / "web-server" / "probe.py").read_text(encoding="utf-8")
    assert _REWRITE_MARKER not in body, (
        "with no readable pin the hook rewrote anyway, i.e. it assumed agreement it never verified"
    )
