---
status: draft
issue: 796
intent: intent/2026-09-29-796-hook-worktree-venv.md
---

# Spec: resolve the hook's Python tooling from the main checkout

## Design

One resolver, run once near the top of `.husky/pre-commit`, feeding all three
sites. It prefers this working tree's own venv and falls back to the main
checkout's:

    # Resolve the backend venv once (#796). A git worktree has no .venv of its
    # own — it is gitignored and lives only in the checkout that created it.
    REPO_ROOT="$(pwd)"
    MAIN_TREE="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
    BACKEND_VENV=""
    for cand in "$REPO_ROOT/apps/backend/.venv" "$MAIN_TREE/apps/backend/.venv"; do
      if [ -d "$cand" ]; then BACKEND_VENV="$cand"; break; fi
    done
    if [ -n "$BACKEND_VENV" ] && [ "$BACKEND_VENV" != "$REPO_ROOT/apps/backend/.venv" ]; then
      echo "  Using the main checkout's venv: $BACKEND_VENV"
      echo "  (this worktree has none; dependencies come from there, not from this branch)"
    fi

`dirname "$(git rev-parse --git-common-dir)"` needs no git-version floor and is
correct both ways, measured on git 2.54.0:

| where | `--git-common-dir` | `dirname` |
| --- | --- | --- |
| main checkout | `.git` (relative) | `.` → repo root |
| worktree | `/…/PFactory/.git` | `/…/PFactory` |

`BACKEND_VENV` is absolute, which also survives the `cd apps/backend` the
pytest section performs before resolving `.venv/bin/pytest`.

The three sites then read from it, keeping their existing Windows `Scripts/`
branches:

- `:99` ruff — `"$BACKEND_VENV/bin/ruff"`, `"$BACKEND_VENV/Scripts/ruff.exe"`, global, skip
- `:170` ratchet python — same shape; and `:194`'s `PATH=` prefix uses
  `$BACKEND_VENV/bin:$BACKEND_VENV/Scripts` instead of `$(pwd)/apps/backend/.venv/…`
- `:258` pytest — `"$BACKEND_VENV/bin/pytest"` / `Scripts/pytest.exe`

Per intent decision 2, when `BACKEND_VENV` is empty **and** the bare-`python`
fallback cannot import pytest, the pytest section blocks with the true cause
instead of "Python tests failed":

    No Python interpreter with pytest in this working tree, and none in the main
    checkout ($MAIN_TREE/apps/backend/.venv).
    No test was run — this is a setup gap, not a test failure.
    Set one up:  cd apps/backend && uv venv && uv pip install -r requirements.txt

## Testability

The resolver moves into `scripts/resolve_backend_venv.sh`, which the hook
sources. This is one added file, and it buys a test that exercises the shipped
artifact rather than a copy of its logic: the test creates a real
`git worktree`, runs the script there, and asserts it resolves to the main
checkout's venv.

## Alternatives rejected

- **Inline the resolver in `.husky/pre-commit`.** Smaller diff, but the only
  reachable test would assert on the hook's *text* (as
  `test_ruff_pin_agreement.py` does for `--package` parity). A text assertion
  passes for any resolver that merely mentions `--git-common-dir`, including a
  broken one — the check-that-measures-nothing pattern this repo has hit
  repeatedly this cycle (#758's `next_seq`, #797's empty detail).
- **A venv per worktree.** Correct in isolation; a full `uv pip install` per
  worktree, hundreds of megabytes duplicated, and slow on first commit.
- **Document the symlink workaround.** Every worktree repeats a manual step,
  and forgetting it presents as a test failure. This is the status quo the
  issue is about.
- **Skip the pytest half when no venv exists.** Rejected by intent decision 2:
  it converts a setup gap into a silently weakened gate.

## Risks

- **A worktree on a branch with different dependencies** silently tests against
  the main checkout's venv. Mitigated, not eliminated, by the announcement
  line — the alternative (refusing to run) is worse than today.
- **`git rev-parse` failing** (not a repo, exotic `GIT_DIR`): `MAIN_TREE`
  collapses to something unusable, the `-d` test fails, and the hook lands in
  the existing "no venv" path. No new failure mode.
- **The hook sources a file that a commit may be modifying.** `sh` reads the
  script as it exists on disk (the working tree), same as the hook itself
  already behaves.

## Verification

1. In a throwaway `git worktree` with no venv: the script resolves to the main
   checkout's venv (test, plus a manual commit that now runs the suite).
2. In the main checkout: resolves to its own venv, no announcement line — the
   current behaviour is unchanged.
3. With neither: the script exits non-zero / leaves `BACKEND_VENV` empty, and
   the hook's message names the missing interpreter.
4. Negative control: point the fallback at a non-existent directory ⇒ test 1
   fails.
