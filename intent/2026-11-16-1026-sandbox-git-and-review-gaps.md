---
status: approved
issue: 1026
author: olafkfreund
---

# Intent: Sandbox remaining git calls and close four review gaps

## Problem

1. `routes/github.py`, `routes/projects.py` and `services/terminal_worktree_service.py` still run bare `git` via `subprocess.run`, inheriting the server's full environment (#1026; `git_run` landed in #1027).
2. `useTaskDetail.ts` log-load effect has no cancelled flag and never clears `phaseLogs` on task or project change: a late response, or the previous task's logs, show under another task.
3. `tests/test_evaluator_integration.py`: `mutation_returncode=1  # KILLED` is never asserted, and the mixed-verdicts test only reads back what its own mock wrote.
4. `plan/service.py` `_decompose`: `check_ceiling(session.run_tokens())` is dead, since `process()` resets the per-run base just before it, so it always checks 0.
5. `plan/service.py` `process()`: a token-ceiling halt sends only a best-effort usage snapshot, not the terminal completion event (sentinel + webhook) that #1027 made carry `halt_reason`.

## Proposed outcome

All git calls in the three files go through `git_run`, guarded by a no-bare-calls test. Log state follows the selected task. The evaluator tests fail if the mutation signal or verdict mapping breaks. The dead check is gone. A ceiling halt emits the completion event.

## Affected users and systems

web-server routes and services, frontend-web task detail, backend plan service, tests. No config or data changes.

## Constraints

Keep diffs focused. No live-cluster changes. Owner delegation 2026-11-16: take the recommended option on open decisions.

## Open questions

None. Decisions (owner delegation): (4) remove the dead check rather than revive it, because the ceiling is per run and `record_usage` already enforces it after each paid call; (5) use `notify_completion`, the existing terminal emitter.
