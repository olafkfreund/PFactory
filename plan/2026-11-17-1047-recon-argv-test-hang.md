---
status: approved
issue: 1047
spec: spec/2026-11-17-1047-recon-argv-test-hang.md
coders:
  1: lead
reviewers:
  - codex
---

# Plan: make the recon argv test unable to block on a grandchild

Decisions: DEVNULL + wait() instead of PIPE + communicate(); parametrize
wait_for_helper False/True (accept() on the listener, bounded 10s, before the kill); per-test 30s timeout; keep all assertions.

## Steps

1. `tests/test_recon_clone_argv_never_carries_the_token.py`: add `import pytest`;
   decorate `test_the_token_is_absent_from_the_recon_child_argv` with
   `@pytest.mark.timeout(30)` and `@pytest.mark.parametrize("wait_for_helper", [False, True])`;
   in the spy use DEVNULL for stdout/stderr, and after the cmdline read, when
   wait_for_helper, `listener.settimeout(10); conn, _ = listener.accept()` (close conn in finally),
   then `proc.kill(); proc.wait()`; comment cites #1047.
   → verify: `apps/backend/.venv/bin/pytest tests/test_recon_clone_argv_never_carries_the_token.py -q`
   (4+1 pass), 40 parallel runs, ruff check + format --check.
   Traps: do not weaken the non-vacuous asserts; the token literal stays assembled.

## Tests

The file above; then `pytest tests/ -m "not slow and not postgres and not docker" -q --timeout=120` is the
inner suite the postgres job runs (full run optional locally).

## Rollback

Revert the commit.
