---
status: approved
issue: 1013, 1023, 1009
author: olafkfreund
---

# Intent: PFactory review-gap fixes

## Problem

1. #1013/#1023: `build_completion_event` rewrites any session whose `process_error` is `TokenCeilingError` to `failed`, even after `discard()` or `reject()` set a terminal status. `failed` is not terminal, so CFactory never closes the work item.
2. #1009: `apps/web-server/server/routes/tasks.py` runs ~21 bare `subprocess.run(["git", ...])` calls (merge, rebase, push, stash, diff, show) with the server's full environment and no exec pins. A hook, merge driver, filter or `core.fsmonitor` planted from a shared worktree runs with `JWT_SECRET`, `APP_DATABASE_URL` and the like.

## Proposed outcome

- A discarded or rejected session's completion event keeps its terminal status.
- Every git call in `routes/tasks.py` runs with `git_env` and the same pins `run_git_command` uses, plus merge-driver/filter pins for merge, rebase, stash, diff and show.
- Each fix has a regression test that fails on current `dev`.

## Affected users and systems

PFactory only: `apps/backend/plan/completion.py`, `apps/web-server/server/services/git_utils.py`, `routes/tasks.py`.

## Constraints

- Smallest change; call semantics (cwd, timeouts, check, exceptions) unchanged.
- Out of scope: the same bare calls in `routes/github.py`, `projects.py`, `terminal_worktree_service.py` (filed as PFactory#1026).

## Open questions

None.
