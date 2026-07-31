"""Every .husky hook must be executable in the index, or git skips it (Factory#921).

`.husky/commit-msg` was committed 100644. Git refuses to run a non-executable
hook and says so only as a `hint:` on stderr, which is not a failure and is
easy to miss:

    hint: The '.../.husky/commit-msg' hook was ignored because it's not set as
    executable.

So the hook had never run in any clone since it was added, while `.husky/pre-commit`
(100755) ran on every commit -- meaning "the hooks passed" and "git skipped a
hook entirely" looked identical.

The mode lives in the index, so this is a property of the repo rather than of
one checkout, and a test can assert it. Same class as AIFactory#1087, TFactory#921.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
# Resolved, not bare: S607 rejects a partial executable path, and a hook test
# that shells out deserves the same care as the thing it is testing.
_GIT = shutil.which("git") or "/usr/bin/git"
# Files under .husky/ that are documentation or config, not hooks.
_NOT_A_HOOK = (".md", ".json", ".yml", ".yaml")


def _husky_entries() -> list[tuple[str, str]]:
    """Return (mode, path) for every tracked file under .husky/."""
    # S603: argv is a fixed literal list with a resolved binary; nothing here
    # comes from input. Reading the INDEX mode is only possible via git.
    out = subprocess.run(  # noqa: S603
        [_GIT, "ls-files", "-s", "--", ".husky/"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    entries = []
    for line in out.stdout.splitlines():
        if not line:
            continue
        meta, path = line.split("\t", 1)
        entries.append((meta.split()[0], path))
    return entries


def test_every_husky_hook_is_executable_in_the_index() -> None:
    entries = _husky_entries()
    assert entries, "expected tracked files under .husky/"

    # Only hook scripts need +x; a README or config under .husky/ does not.
    not_executable = [
        path for mode, path in entries if not path.endswith(_NOT_A_HOOK) and mode != "100755"
    ]
    assert not not_executable, (
        "git silently SKIPS a hook that is not executable, printing only a "
        f"'hint:' -- so these have never run: {not_executable}. "
        "Fix with: git update-index --chmod=+x <path>"
    )


def test_commit_msg_accepts_the_conventions_this_repo_actually_uses() -> None:
    """The type list must cover real history, or enabling the hook blocks releases.

    `security:` (10x) and `release:` (2x) appear in the last 400 commits and
    were absent from the pattern. A hook that never runs drifts out of step
    with the convention it exists to enforce, and only bites at the moment
    someone switches it on.
    """
    hook = _REPO / ".husky" / "commit-msg"
    pattern_line = next(
        line
        for line in hook.read_text(encoding="utf-8").splitlines()
        if line.startswith("pattern=")
    )
    for required in ("release", "security", "feat", "fix", "chore", "revert"):
        assert f"{required}|" in pattern_line or f"|{required})" in pattern_line, (
            f"commit-msg rejects '{required}:', which this repo's history uses"
        )
