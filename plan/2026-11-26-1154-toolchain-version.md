---
status: approved
issue: 1154
spec: spec/2026-11-26-1154-toolchain-version.md
coders:
  1: codex   # small, exact parsing and selection logic with an edge-case test table
reviewers:
  - codex    # checks the resolver against the spec's table
  - agy      # independent read of the parsing and logging safety
---

# Plan: resolve detected versions into exact toolchain versions

## Approved decisions (from the spec)

- **One helper.** `contract_toolchain(versions)` in
  `apps/backend/plan/emit/environment_block.py` replaces `env["toolchain"] = dict(versions)` at both
  sites (~line 87 and ~line 162). The `toolchain` key is set only when the result is non-empty.
- **Input rules:**
  - a non-string value is dropped;
  - whitespace is stripped;
  - an exact version is `re.fullmatch(r"[0-9]{1,4}(\.[0-9]{1,4}){0,2}", v)`.
- **Per key:**
  - an exact value passes through, except that Go below 1.21 is dropped (`go_too_old`);
  - a Python value that isn't exact goes to `_resolve_python`;
  - a Go value that isn't exact is dropped;
  - other keys pass through when they are strings.
- **Logging:** each omission logs one warning with the key and a reason code (`non_string`,
  `unparseable`, `unsatisfiable`, `go_too_old`). The raw value never appears in a log.
- **No schema change.** The raw value stays in `baseline.versions`.
- **`_resolve_python(spec)`:**
  - Split the specifier into clauses on `,`. Each clause is an operator (`~=`, `==`, `!=`, `<=`,
    `>=`, `<`, `>`) followed by a version. `.*` is allowed only after `==` and `!=`, and `~=` needs
    at least 2 components. Anything else is `unparseable`.
  - The candidates are the minors `3.8` to `3.14`.
  - A minor `X.Y` satisfies a clause when both `X.Y.0` and `X.Y.9999` do. A three-component
    `!= V` with `V[:2] == (X, Y)` rejects that minor.
  - Pick the newest satisfying minor in `_NIX_CACHED_PYTHON = ("3.14", "3.13", "3.12")`.
    Otherwise pick the lowest satisfying minor. Otherwise the result is `unsatisfiable`.
- **Satisfaction on padded integer tuples:**

  | Operator | Satisfied when |
  |---|---|
  | `>=`, `>`, `<=`, `<` | the tuple comparison holds |
  | `==` | the tuples are equal |
  | `!=` | the tuples are not equal |
  | `== V.*` | the prefix matches |
  | `!= V.*` | the prefix doesn't match |
  | `~= V` | `>= V` and `== V[:-1].*` |
- **`_NIX_CACHED_PYTHON`** gets a comment pointing to `Factory/scripts/nix_provisioner.py`
  `_PY_ATTR` and #1020, and telling maintainers to update it when `_PY_ATTR` changes.

## Steps

1. **Code and tests** in `apps/backend/plan/emit/environment_block.py` and
   `tests/test_environment_block.py`.

   **Code:**
   - The module has no logger yet. Add `import logging`, `import re` and
     `logger = logging.getLogger(__name__)`.
   - Parsing, exactly:
     - Strip the whole value, then split it on `,`.
     - Strip each clause, and reject an empty clause.
     - Match each clause with
       `re.fullmatch(r"(~=|==|!=|<=|>=|<|>)\s*([0-9]{1,4}(?:\.[0-9]{1,4}){0,2})(\.\*)?", clause)`.
       Allow `.*` only with `==` and `!=`, and require at least 2 components for `~=`.
   - Tuple comparisons:
     - Pad to 3 components for `<`, `<=`, `>`, `>=`, `==` and `!=`.
     - For `.*` and `~=` prefixes, compare only the operand's own component count. Don't pad
       those.

   **Tests:** add the full verification table below, as one parametrized test.

   | Input | Result |
   |---|---|
   | `>=3.11` | `3.14` |
   | `>= 3.11, < 3.13` | `3.12` |
   | `>=3.11,!=3.14.*` | `3.13` |
   | `~=3.12` | `3.14` |
   | `==3.12.*` | `3.12` |
   | `==3.12.0` | omitted (`unsatisfiable`) |
   | `>=3.12,!=3.14.1` | `3.13` |
   | `>=3.10,<3.12` | `3.10` |
   | `>=3.10,!=3.10.*,<3.12` | `3.11` |
   | `<3.12` | `3.8` |
   | `>3.13,<3.14` | omitted (`unsatisfiable`) |
   | `stable`, `system`, `>=3.11rc1`, `>=`, `>=3.11,`, `٣.١٢`, a 5,000-digit component | omitted (`unparseable`) |
   | `3.12` | `3.12` |
   | `" 3.12\n"` | `3.12` |
   | `3` (an int) | omitted (`non_string`) |

   Also add:
   - Go: `1.25` is kept; `1.25rc1` is dropped (`unparseable`); `1.20` is dropped (`go_too_old`).
   - Node, Rust and Java string values pass through unchanged.
   - Both emission paths through `derive_environment`, each asserting the resolved toolchain, an
     unchanged `baseline.versions`, and that the `toolchain` key is absent when every value is
     dropped:
     - the **descriptor path** sets `tfactory.language` to a language the descriptors resolve
       (`"python"`);
     - the **legacy path** omits `tfactory.language`, as the existing legacy cases do.
   - Logging: each of the four reason codes is logged with its key, and the raw value appears in
     no record.
   - The resolved environment validates against the contract schema, using the file's existing
     schema helper.
   - `_NIX_CACHED_PYTHON` is ordered newest first, and every entry matches the exact-version
     pattern. Its source comment cites `_PY_ATTR` and explains why #1020 excludes 3.11.

   **Trap:** the file's `__main__` runner (~line 92) calls every `test_*` function with no
   arguments. Write the table as a module-level list, with one zero-argument `test_*` function
   that loops over it. Wrap any `caplog` or parametrize usage in pytest-only tests that the
   runner skips: name them `_pytest_*`, or guard the runner with
   `inspect.signature(fn).parameters`.

   **Verify** (all from `apps/backend`, with `P=.venv/bin/pytest`, or
   `/mnt/code/Source-home/GitHub/PFactory/apps/backend/.venv/bin/pytest` if the worktree has no
   venv):
   - `$P -q ../../tests/test_environment_block.py -p no:cacheprovider` passes.
   - `python3 ../../tests/test_environment_block.py` still passes, which exercises the direct
     runner.
   - `$P -q ../../tests -k "contract or schema or environment" -p no:cacheprovider`, run the same
     way in a clean `origin/dev` checkout, shows no new failures.
   - `ruff check` and `ruff format --check` pass on both files.

   **Other traps:**
   - Stdlib only.
   - Keep both emission paths.
   - Don't touch `reconnoiter.py`.
   - Never log the value.
   - The PR target is `dev`, which is PFactory's integration branch.

2. **Lead only:**
   - review the diff against the spec table;
   - commit, and open a PR to `dev`;
   - merge with `--match-head-commit` after CI passes;
   - release PFactory (its normal release PR to `main`, then the CD deploy);
   - confirm the health version;
   - rerun E2E P1 and check that a verdict exists, then S4 and S5.

## Tests

- The step 1 pytest commands pass, with no new failures compared with the dev baseline.
- CI is green.
- Live: P1 run gets a TFactory verdict, and the PR merges.

## Rollback

1. Revert the PR on `dev` and release a patch.
2. After the revert, newly emitted contracts carry raw specifiers again, so the original
   verification failure comes back for ranged `requires-python`.
3. Contracts signed while the fix was live carry exact versions such as `3.14`. The current
   provisioner accepts these (`_PY_ATTR` and `_checked_version`), so they need no action.

After the fix deploys, contracts signed before it still carry `>=3.11`. They must be re-planned to
verify. Old E2E runs are not re-run; each new P1 run plans afresh.
