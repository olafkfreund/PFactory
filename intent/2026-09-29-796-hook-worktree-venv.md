---
status: draft
issue: 796
---

# Intent: the pre-commit hook finds no Python tooling in a git worktree

## Problem

`.husky/pre-commit` resolves its Python tooling relative to the current working
directory, at three sites:

| line | what it resolves | fallback when absent |
| --- | --- | --- |
| :99 | `apps/backend/.venv/bin/ruff` | global `ruff`, then skip |
| :170 | `apps/backend/.venv/bin/python` (ratchet) | `python3` |
| :258 | `apps/backend/.venv/bin/pytest` | `python -m pytest` |

A git worktree has no `.venv` — it is gitignored and exists only in the
checkout where it was created. Measured in a throwaway worktree of this repo
(`git worktree add --detach`):

    $ ls apps/backend/.venv
    ls: cannot access 'apps/backend/.venv': No such file or directory
    $ command -v python3
    /nix/store/…-python3-3.13.13-env/bin/python3
    $ python3 -c "import pytest"
    ModuleNotFoundError: No module named 'pytest'

and the main checkout is reachable from there without hardcoding anything:

    $ git rev-parse --git-common-dir
    /mnt/data/Source-home/GitHub/PFactory/.git      # parent is the main tree
    $ git rev-parse --git-dir
    /mnt/data/Source-home/GitHub/PFactory/.git/worktrees/wt796 The ruff half degrades honestly (the #452
version guard skips the rewrite and says so). The pytest half does not: the
bare-`python` fallback has no pytest, so the commit is blocked with

    Python tests failed. Please fix failing tests before committing.

which names the wrong cause. The working tree has no interpreter; no test
failed.

## Outcome

Committing from a worktree of this repo runs the same hook checks as committing
from the main checkout, using the main checkout's venv. When no venv is
findable anywhere, the hook says that — a missing interpreter and a failing
test call for completely different actions.

## Affected

- `.husky/pre-commit` — the three resolution sites above.
- Every parallel session: this repo currently has six worktrees, and the
  `PFactory-*` checkouts plus `.claude/worktrees/*` all hit this. Observed
  while working on #765 and #780.

## Constraints

- No new venv per worktree: a full `uv pip install` per worktree is slow and
  duplicates hundreds of megabytes.
- The main checkout's path must not be hardcoded. `git rev-parse
  --git-common-dir` yields the main checkout's `.git`; its parent is the main
  working tree.
- The hook must keep working unchanged in the main checkout, in CI, and where
  no venv exists at all.
- Do not widen the fix into the ratchet's `--package` parity — that was #786,
  already merged.

## Decisions (were open questions, answered at approval)

1. **The hook announces a cross-tree venv.** One line naming the venv it
   resolved to. A worktree on a branch with different dependencies would
   otherwise be tested silently against the wrong ones, which is the kind of
   check-that-measures-nothing this repo keeps finding.
2. **A venv findable nowhere keeps blocking, with a corrected message.**
   Skipping would turn a setup gap into a silently weakened gate. The message
   must say the working tree has no interpreter, not that tests failed — the
   two call for completely different actions.
