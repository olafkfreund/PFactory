---
status: approved
issue: 840
spec: spec/2026-10-21-840-precommit-skip-docker-tests.md
---

# Plan: keep docker-daemon tests out of the pre-commit hook

Approved decisions (from the spec):

- Ignore `tests/docker/` via the existing `IGNORE_TESTS` list in `.husky/pre-commit`,
  not via a marker expression and not by changing the image tag.
- One comment line says why (shared daemon state) and that CI's docker job gates it.
- A regression test parses the real hook's `IGNORE_TESTS`.

## Steps

1. `.husky/pre-commit`: add a comment line `#   docker/: builds a shared-tag image on
   the host daemon; flaky under concurrent builds (#840). CI's docker job gates it.`
   and append `--ignore=../../tests/docker/` to `IGNORE_TESTS`.
2. Add `tests/test_precommit_ignores_docker.py`: extract the `IGNORE_TESTS="..."`
   value from the hook, resolve each `--ignore=` path against `apps/backend`, assert
   `tests/docker` is among them; assert every test file containing `mark.docker` is
   under `tests/docker/`.

## Tests

`pytest tests/test_precommit_ignores_docker.py` fails before step 1, passes after.
`ruff check` / `ruff format --check` on the new file.

## Rollback

Revert the commit; the hook returns to running docker tests.

## Deviations

- Steps 1 and 2 were done by the session model: agy-implement returned a
  content-filter refusal with no edits.
- Step 2 matches the marker as a decorator or `pytestmark =` line (regex), not the
  bare substring, so the test does not match its own source.
