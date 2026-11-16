---
status: approved
issue: 1013, 1023, 1009
intent: intent/2026-11-16-review-gap-fixes-pfactory.md
---

# Spec: PFactory review-gap fixes

## Design

1. `completion.py`: apply the TokenCeilingError override only when `status == "failed"`. discard()/reject() leave `process_error` set, so the status check is the minimal fix; no state mutation.
2. `services/git_utils.py`: add `git_run(cmd, **kwargs)`, a drop-in for `subprocess.run` on a git argv. It passes `env=git_env(keep=(*GITHUB_KEEP, "SSH_AUTH_SOCK"))` (as `run_git_command` does), adds the remote pins for fetch/pull/push, and for merge, rebase, stash, diff, show, merge-tree prepends `merge_driver_pins(cwd)` (fails closed with RuntimeError); for diff/show it also adds `--no-ext-diff --no-textconv`, which merge_driver_pins does not cover (Codex review finding). `routes/tasks.py` replaces its 21 bare git `subprocess.run` calls with `git_run`.

## Alternatives rejected

- Convert each call to `run_git_command`: it has a fixed 30s timeout and returns a dict, so merge/rebase/push semantics and exceptions would change.
- Clear `process_error` in discard()/reject(): touches service state and misses sessions already persisted.

## Risks

- `merge_driver_pins` runs one extra `git config` per call; a failure now raises instead of running unpinned (intended).

## Verification

- `tests/test_plan_completion.py`: discarded/rejected keep status (fails before).
- `apps/web-server/tests/test_tasks_git_sandbox.py`: no bare git `subprocess.run` left in tasks.py (fails before); `git_run` env drops server secrets.
- Existing web-server task/git tests pass.
