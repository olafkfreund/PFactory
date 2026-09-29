---
status: draft
issue: 796
spec: spec/2026-09-29-796-hook-worktree-venv.md
---

# Plan: resolve the hook's Python tooling from the main checkout

Approved decisions (from the intent and spec):

- Prefer this working tree's `apps/backend/.venv`; fall back to the main
  checkout's, located with `dirname "$(git rev-parse --git-common-dir)"` — no
  git-version floor, correct in both a worktree and the main checkout.
- `BACKEND_VENV` is **absolute**, so it survives the `cd apps/backend` in the
  pytest section.
- A cross-tree venv is **announced** (intent decision 1).
- No venv anywhere still **blocks**, with a message naming the missing
  interpreter rather than "Python tests failed" (intent decision 2).
- The resolver lives in `scripts/resolve_backend_venv.sh`, sourced by the hook,
  so the test exercises the shipped artifact rather than a copy of its logic.
- Out of scope: the ratchet's `--package` parity (#786, already merged).

## Steps

1. **`scripts/resolve_backend_venv.sh`** (new). Sets `BACKEND_VENV` (absolute,
   or empty) and `MAIN_TREE`; prints the announcement line only when it
   resolved to a tree other than the caller's. Sourced, so it must not `exit`
   — an unresolvable venv leaves `BACKEND_VENV` empty and returns non-zero.
2. **`.husky/pre-commit`** — source the script once, before the ruff section,
   and replace the four hardcoded prefixes:
   - `:99` ruff → `"$BACKEND_VENV/bin/ruff"` / `Scripts/ruff.exe`, then global,
     then skip (unchanged order)
   - `:170` ratchet python → same shape, `python3` fallback kept
   - `:194` `PATH=` → `$BACKEND_VENV/bin:$BACKEND_VENV/Scripts:$PATH`
   - `:258` pytest → `"$BACKEND_VENV/bin/pytest"` / `Scripts/pytest.exe`
   Each site must tolerate an empty `BACKEND_VENV` (the `-f` tests simply fail
   and it falls through as today).
3. **The pytest failure message** — when no venv resolved and `python -m pytest`
   is unavailable, say the working tree has no interpreter, name
   `$MAIN_TREE/apps/backend/.venv`, and give the `uv venv` line. Exit 1.
4. **`tests/test_hook_venv_resolution.py`** (new), following
   `tests/test_ruff_pin_agreement.py`'s `_REPO = Path(__file__).resolve().parents[1]`
   convention:
   a. `git worktree add --detach` into `tmp_path`, run
      `sh -c '. scripts/resolve_backend_venv.sh; echo "$BACKEND_VENV"'` with cwd
      in the worktree → resolves to `_REPO/apps/backend/.venv`, and stdout
      carries the announcement;
   b. same script run in `_REPO` → resolves to `_REPO/apps/backend/.venv` with
      **no** announcement line (main-checkout behaviour unchanged);
   c. a tree where neither venv exists (`HOME`-less temp clone, or a monkeyed
      candidate path) → `BACKEND_VENV` empty, non-zero return;
   d. the hook actually sources the script — assert `.husky/pre-commit`
      references `resolve_backend_venv.sh` and no longer contains the literal
      `apps/backend/.venv/bin/pytest`.
   Every worktree created is removed in a `finally` (`git worktree remove
   --force`), so a failing test leaves no worktree behind.
5. **Negative control** (not committed): point the fallback candidate at a
   non-existent directory ⇒ case (a) fails. Restore.
6. **Manual end-to-end**: in a throwaway worktree with a staged trivial Python
   edit, run `git commit` and confirm the suite runs instead of
   "No module named pytest". This is the behaviour the issue reports, and no
   unit test covers the hook end to end.

## Tests

    apps/backend/.venv/bin/pytest tests/test_hook_venv_resolution.py -q
    apps/backend/.venv/bin/pytest tests/test_ruff_pin_agreement.py -q
    shellcheck scripts/resolve_backend_venv.sh .husky/pre-commit   # if available
    sh -n .husky/pre-commit && sh -n scripts/resolve_backend_venv.sh

Expected: new tests pass; the existing hook-parity tests still pass; both shell
files parse.

## Rollback

Revert the commit: the hook returns to the hardcoded `apps/backend/.venv`
prefixes and `scripts/resolve_backend_venv.sh` disappears. Nothing persists
outside the two files and the new test.

## Risk at implementation time

This change edits the hook that gates its own commit. If step 2 breaks the
hook, committing the fix may itself fail. Mitigation: `sh -n` both files before
staging, and keep `git commit --no-verify` available for the recovery commit —
disclosed here rather than discovered mid-commit.
