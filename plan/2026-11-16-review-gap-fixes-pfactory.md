---
status: draft
issue: 1013, 1023, 1009
spec: spec/2026-11-16-review-gap-fixes-pfactory.md
coders:
  1: you
  2: you
  3: you
reviewers:
  - codex
---

# Plan: PFactory review-gap fixes

Decisions (from the spec): override TokenCeilingError only when status is failed; add `git_run` in `services/git_utils.py` (git_env, remote pins, merge-driver pins, plus `--no-ext-diff --no-textconv` for diff/show (Codex review finding) for merge/rebase/stash/diff/show/merge-tree) and use it for every git call in `routes/tasks.py`. Reasons for `you`: three small edits, a handoff costs more than it saves.

## Steps

1. `apps/backend/plan/completion.py:141`: add `status == "failed" and` to the condition → `pytest tests/test_plan_completion.py`. Traps: none.
2. `apps/web-server/server/services/git_utils.py`: add `git_run` above `run_git_command`; import `merge_driver_pins` → web-server git tests. Traps: ruff.
3. `apps/web-server/server/routes/tasks.py`: replace 21 bare git `subprocess.run(` with `git_run(`; import it → `pytest apps/web-server/tests -k "task or git"`. Traps: leave `subprocess.Popen` and exception references alone.

## Tests

`apps/backend/.venv/bin/pytest tests/test_plan_completion.py apps/web-server/tests/test_tasks_git_sandbox.py`, then ruff check on touched files.

## Rollback

Revert the PR; no data or config changes.
