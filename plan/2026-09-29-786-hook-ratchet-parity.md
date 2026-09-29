---
status: approved
issue: 786
spec: spec/2026-09-29-786-hook-ratchet-parity.md
---

# Plan: The pre-commit ratchet does not gate what CI gates

Approved decisions (from the spec):

- `.husky/pre-commit` gains `--package scripts`, matching CI's three.
- `ratchet_lint.py`'s success line names the half that ran:
  `" (ruff only; mypy runs in CI)"` when mypy was skipped.
- New `--mypy` flag overrides the staged default
  (`no_mypy = args.no_mypy or (args.staged and not args.mypy)`); `--mypy` with
  `--no-mypy` is a parser error. The hook passes it only when
  `PFACTORY_HOOK_MYPY` is truthy. Default behaviour, and its speed, unchanged.
- `--mypy`'s help carries the caveat: in staged mode `mypy_errors()` reads the
  file on disk, so a partially staged file is judged as it sits, not as staged.
- Measured, to be quoted in the hook comment: mypy on one web-server file is
  12.0s cold, 0.4s warm.

Existing seams to extend rather than duplicate:
`tests/test_ruff_pin_agreement.py` already parses CI's package set
(`_ratchet_packages()`, with `${PACKAGE_DIR}` resolved);
`tests/test_ratchet_staged.py` already drives the script as a subprocess against
a throwaway git repo with ruff on `PATH`.

## Steps

0. Symlink `apps/backend/.venv` to the main checkout's (this worktree has none —
   #796), so the hook and tests can run at all.
1. `.husky/pre-commit:177`: add `--package scripts`; note in the comment that the
   three packages must match `cq-ratchet.yml`, with the parity test named.
   → verify by step 5's test failing before and passing after.
2. `scripts/ratchet_lint.py:545`: the suffix names the half that ran.
   → verify by step 6a.
3. `scripts/ratchet_lint.py`: add `--mypy` (help text carries the on-disk
   caveat); `no_mypy = args.no_mypy or (args.staged and not args.mypy)`; parser
   error when both flags are given. → verify by steps 6b and 6c.
4. `.husky/pre-commit`: when `PFACTORY_HOOK_MYPY` is truthy, append `--mypy`;
   comment records the measured cost and the caveat. → verify by step 7's
   measurement.
5. `tests/test_ruff_pin_agreement.py`: add
   `test_the_hook_gates_the_same_packages_as_ci` — parse `--package` flags from
   `.husky/pre-commit`, compare with `_ratchet_packages()`; the docstring records
   that this drift let #778/#785 through the hook.
6. `tests/test_ratchet_staged.py`: extend `_ratchet(repo, *extra)` to take flags;
   add `_mypy_dir()` + skip when mypy is absent, then:
   a. a staged pass prints "(ruff only; mypy runs in CI)";
   b. with `--mypy`, a staged file carrying a net-new `mypy --strict` error is
      BLOCKED, and the default run passes it — the #778/#785 case, locally;
   c. `--staged --mypy --no-mypy` exits 2 (parser error).
7. Measure the opt-in run (cold and warm) and put the real numbers in the hook
   comment, replacing the per-file figures if they differ.
8. Negative controls (not committed): revert step 1 → step 5 fails; revert
   step 2 → 6a fails; revert step 3's override → 6b fails. Restore each.

## Tests

    apps/backend/.venv/bin/pytest tests/test_ratchet_staged.py tests/test_ruff_pin_agreement.py \
      tests/test_ratchet_renames.py tests/test_ratchet_test_bar.py tests/test_ratchet_tool_failure.py -q
    # the hook itself, on a real staged change:
    git commit -s -m "..."            # default: ruff-only, fast
    PFACTORY_HOOK_MYPY=1 git commit   # opt-in: mypy too

Expected: all pass; the full backend suite runs in the pre-commit hook.

## Rollback

Revert the commit. The hook returns to two packages and a silent ruff-only
pass; `--mypy` disappears. No state, no schema, CI unaffected throughout.
