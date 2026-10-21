---
status: approved
issue: 840
intent: intent/2026-10-21-840-precommit-skip-docker-tests.md
---

# Spec: keep docker-daemon tests out of the pre-commit hook

## Design

Add `--ignore=../../tests/docker/` to `IGNORE_TESTS` in `.husky/pre-commit`, with one
line in the comment above it naming why: the tests depend on shared docker daemon
state (a fixed image tag other builds can retag), and CI's docker job gates the image.
Every `@pytest.mark.docker` test lives under `tests/docker/`, so a directory ignore
covers them all.

## Alternatives rejected

- `-m "... and not docker"` in the three pytest calls: equivalent today, but touches
  three lines instead of one and breaks the hook's single ignore-list convention.
- Unique per-session image tag in `conftest.py`: fixes the retag race but the hook
  would still build a full image (minutes) on every backend commit.

## Risks

A docker-only regression is now caught in CI rather than at commit time. Accepted:
CI's docker job already runs these tests.

## Verification

New test `tests/test_precommit_ignores_docker.py` reads `IGNORE_TESTS` from the real
hook and asserts it ignores `tests/docker/` (resolved from the hook's `apps/backend`
cwd to the real directory), and that no docker-marked test lives outside that
directory. It fails on the current hook.
