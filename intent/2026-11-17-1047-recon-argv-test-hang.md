---
status: approved
issue: 1047
author: olafkfreund
---

# Intent: recon-argv test hangs on slow runners and reds the postgres acceptance job

## Problem

`postgres (P1 acceptance, PG 15/16)` failed on attempt 1 and passed on rerun for
#1045 (run 44094728441) and #1046 (run 44099987305). In both attempts the failing
test is `tests/postgres/test_p1_suite_against_postgres.py::test_full_pytest_suite_passes_against_postgres`,
and its inner run reports exactly one failure:
`tests/test_recon_clone_argv_never_carries_the_token.py::test_the_token_is_absent_from_the_recon_child_argv - Failed: Timeout (>120.0s)`
(5991 passed). So it is not a Postgres race; it is a hang in that one test.

Root cause (reproduced locally, deterministic): the test spawns a real
`git clone` with stdout/stderr as pipes, reads `/proc/<pid>/cmdline`, then
`kill()`s git and calls `communicate()`. If git has already spawned
`git-remote-https` (it inherits the stderr pipe and is blocked talking to a
listener that never replies), `communicate()` waits for the grandchild to close
the pipe. The listener is only closed after the `with`, so it never does: hang
until pytest-timeout. On a fast machine the kill lands before the helper
exists, so it passes. Inserting a 1s delay before the kill reproduces the hang.

## Proposed outcome

The test cannot hang however slow the host is, and a regression check covers
the "helper already spawned" timing.

## Affected users and systems

Test code only (CI reliability of the postgres acceptance job).

## Constraints

Keep the security property under test (token absent from argv and
/proc/<pid>/cmdline) and its non-vacuous guards. No blanket rerun.

## Open questions

None.
