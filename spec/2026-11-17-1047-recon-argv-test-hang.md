---
status: draft
issue: 1047
intent: intent/2026-11-17-1047-recon-argv-test-hang.md
---

# Spec: make the recon argv test unable to block on a grandchild

## Design

In `tests/test_recon_clone_argv_never_carries_the_token.py`:

- Spawn git with `stdout/stderr=subprocess.DEVNULL` (nothing is read from them)
  and replace `communicate()` with `wait()`, which only waits on the killed
  process, never on a grandchild holding a pipe.
- Parametrize the main test on `wait_for_helper` (False/True). When True the
  spy first `accept()`s on the listener (bounded 10s): a connection proves
  `git-remote-https` is up and holding the inherited fds, so the former hang
  window is exercised deterministically, not by a sleep. Plus
  `@pytest.mark.timeout(30)` so any regression fails fast instead of eating the
  120s suite timeout.

## Alternatives rejected

- Kill the process group: extra moving parts; DEVNULL removes the cause.
- Close the listener before reaping: ordering trick that leaves the pipe wait.
- Rerun-on-failure in CI: hides the cause.

## Risks

The True case waits for git's connect (milliseconds). A lingering helper is reset when the listener closes
at the end of the test.

## Verification

Before: with a 1s pre-kill delay the test times out (reproduced). After: both
parametrized cases pass, 40 parallel runs pass, ruff clean.
