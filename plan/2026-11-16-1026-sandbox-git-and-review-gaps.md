---
status: approved
issue: 1026
spec: spec/2026-11-16-1026-sandbox-git-and-review-gaps.md
coders:
  1: you
  2: you
  3: you
  4: you
  5: you
reviewers:
  - codex
---

# Plan: Sandbox remaining git calls and close four review gaps

Decisions from the spec: route the remaining bare git calls through `git_run` with a no-bare-calls test; fix the log effect with a cancelled flag and clearing on change; make the evaluator mock derive its mutation signal from the prompt and assert it; remove the dead `check_ceiling` in `_decompose`; call `notify_completion` on a TokenCeilingError halt. Reason for `you` on every step: each is a small edit and a handoff costs more than it saves.

## Steps

1. `apps/web-server/server/services/terminal_worktree_service.py:348`, `routes/projects.py:961,974,1020,1032`, `routes/github.py:1094`: use `git_run`. In `_run_git_command` pass `cmd` unchanged. In projects.py drop the `import subprocess`. In github.py add `RuntimeError` to the handled errors in `add_git_remote`. New test in `apps/web-server/tests/test_tasks_git_sandbox.py` scanning github.py/projects.py for git `subprocess.run` and terminal_worktree_service.py for any `subprocess.run(` → `pytest apps/web-server/tests -k "git or worktree or project or github"`. Traps: github.py still needs `subprocess` for the gh install and `TimeoutExpired`; ruff import order.
2. `apps/frontend-web/src/components/task-detail/hooks/useTaskDetail.ts:190-245`: cancelled flag, clear `phaseLogs` and `expandedPhases` at effect start, guard sets; add a deferred-response vitest if the hook has a test harness → `npx tsc --noEmit` and vitest for the hook in `apps/frontend-web`.
3. `tests/test_evaluator_integration.py`: mock reads the mutation verdict from the prompt block; assert the exact per-id mapping (3 killed, 3 no_mutation) in the happy test; add a `mutation_returncode=0` test (3 survived, 3 no_mutation); strengthen the mixed test → `pytest tests/test_evaluator_integration.py`.
4. `apps/backend/plan/service.py:1703`: delete the dead check → `pytest tests/test_run_guard.py`.
5. `apps/backend/plan/service.py:1374`: `notify_completion(session)` for TokenCeilingError; import already present. Add a test in `tests/test_run_guard.py` enabling `PFACTORY_COMPLETION_SENTINEL` + `_DIR` (tmp_path) and asserting `COMPLETED.json` has failed status and a token_ceiling halt_reason → `pytest tests/test_run_guard.py tests/test_plan_completion.py`.

## Tests

`apps/backend/.venv/bin/pytest` on the files named above, frontend tsc/vitest/eslint on the hook, `ruff check` and `ruff format --check` on touched Python files.

## Rollback

Revert the PR; no data or config changes.
