---
status: draft
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

1. Implement the decisions above in `apps/backend/plan/emit/environment_block.py`, using
   `logging.getLogger(__name__)` as the module already does.

   In `tests/test_environment_block.py`, add:
   - a parametrized test covering every row of the spec's verification table;
   - the Go cases `1.25` (kept), `1.25rc1` (dropped) and `1.20` (dropped);
   - a Node/Rust/Java passthrough test;
   - a test for each emission path, through `derive_environment`, with a contract that has
     `baseline.versions`. Each asserts the resolved toolchain and that the raw
     `baseline.versions` is unchanged.
   - a test that no `toolchain` key appears when nothing resolves;
   - a `caplog` test showing the key and reason code are logged and the raw value is not;
   - a test that the resolved environment still validates against the contract schema, using the
     existing schema test helper;
   - a shape test for `_NIX_CACHED_PYTHON`.

   Verify:
   - `cd apps/backend && .venv/bin/pytest -q ../../tests/test_environment_block.py -p no:cacheprovider`
     passes. If the worktree has no `.venv`, use
     `/mnt/code/Source-home/GitHub/PFactory/apps/backend/.venv/bin/pytest`.
   - Then run the contract-emit and schema tests (`pytest -q ../../tests -k "contract or schema or environment"`)
     and compare the result with a clean `origin/dev` run.
   - `ruff check` and `ruff format --check` pass on both files.

   Traps:
   - Stdlib only.
   - Keep both emission paths.
   - Don't touch `reconnoiter.py`.
   - Never log the value.

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

Revert the PR on `dev` and release a patch. Contracts signed during the window carry exact
versions, which the old consumers also accept, so no data migration is needed.
