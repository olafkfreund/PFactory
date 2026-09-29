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
checkout where it was created. The ruff half degrades honestly (the #452
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

## Open questions

1. When the main checkout's venv is found from a worktree, should the hook say
   so, or resolve silently? (A silent cross-tree venv could surprise someone
   whose worktree is on a branch with different dependencies.)
2. Should a missing venv make the pytest half **skip with a clear message**, or
   keep **blocking** with a corrected message? Skipping weakens the gate on a
   machine that never had a venv; blocking keeps it, at the cost of refusing
   commits until setup is done.
