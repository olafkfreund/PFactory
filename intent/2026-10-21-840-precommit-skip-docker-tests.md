---
status: approved
issue: 840
author: olafkfreund
---

# Intent: keep docker-daemon tests out of the pre-commit hook

## Problem

`.husky/pre-commit` runs the backend suite with `-x -m "not slow and not integration"`
and does not ignore `tests/docker/`. Those tests build and inspect the image under a
fixed shared tag (`pfactory:p0-test`, `tests/docker/conftest.py`), so their result
depends on daemon state the test does not control: a concurrent build on the host can
retag the image. `test_pip_absent_from_final_image` failed once this way and, because
of `-x`, rejected an unrelated commit after a ~7 minute wait (#840).

## Outcome

A local commit never fails on a docker-marked test. The image is still gated by CI's
docker (P0 acceptance) job.

## Affected

Developers committing backend changes; `.husky/pre-commit`.

## Constraints

- Keep the hook's existing ignore-list pattern.
- Do not weaken CI: the docker job keeps running these tests.

## Open questions

None.
