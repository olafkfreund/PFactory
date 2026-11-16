---
status: approved
issue: 1026
intent: intent/2026-11-16-1026-sandbox-git-and-review-gaps.md
---

# Spec: Sandbox remaining git calls and close four review gaps

## Design

1. Replace `subprocess.run(["git", ...])` with `git_run([...])` at: github.py `add_git_remote`; projects.py `list_project_worktrees` (4 calls); terminal_worktree_service.py `_run_git_command`. Keep `check`, `cwd`, `capture_output`, `text`, `timeout` kwargs. Exception types are unchanged (`CalledProcessError`, `TimeoutExpired`); `git_run` can also raise `RuntimeError` (fail closed), which the existing broad excepts in projects.py cover; in github.py `add_git_remote` it is added to the handled errors. Add `test_no_bare_git_subprocess_in_routes_and_services` beside `test_tasks_git_sandbox.py`, scanning github.py and projects.py for `subprocess.run(` with a `"git"` argv, and asserting terminal_worktree_service.py has no `subprocess.run(` at all (its call passes a variable argv).
2. `useTaskDetail.ts`, BOTH log effects (the reload effect near line 124 and the load/watch effect near line 190): `let cancelled = false`; clear `phaseLogs` (and expanded phases) at effect start; guard every state set after `await` and inside the change listener with `!cancelled`; set `cancelled = true` in cleanup.
3. Evaluator integration: the SDK mock derives `signals_summary.mutation` from the `- mutation: <verdict>` line in each test's prompt block instead of the literal "killed". The six fixtures yield three mutated tests and three `no_mutation` (the probe returns before running the runner), so the happy test asserts the exact per-id mapping: three "killed", three "no_mutation". A new test with `mutation_returncode=0` asserts "survived". The mixed test asserts the per-id verdict mapping and that the prompt reached the evaluator for all six ids.
4. Delete the dead `check_ceiling` line in `_decompose`; keep a one-line comment pointing to `record_usage`.
5. In `process()`'s `TokenCeilingError` branch call `notify_completion(session)` instead of `emit_usage_snapshot(session)`. Test enables the sentinel in a tmp dir and asserts `COMPLETED.json` exists with `status=failed` and `halt_reason` starting `token_ceiling` (the snapshot never wrote the sentinel, so it fails on current code).

## Alternatives rejected

- (4) Revive the check with a session-lifetime total: contradicts #1525 (per-run ceiling).
- (5) Add `failed` to `TERMINAL_STATUSES`: would fire completion for every failure and change other callers.
- (1) Wrap in `run_git_command`: returns a dict, so exceptions that callers handle would change.

## Risks

`git_run` on `diff` calls `merge_driver_pins(cwd)`, which fails closed with `RuntimeError`; projects.py already catches `Exception` there. Terminal worktree callers catch only `CalledProcessError`; a `RuntimeError` surfaces as a 500, which is the fail-closed intent.

## Verification

web-server tests, `tests/test_evaluator_integration.py`, `tests/test_run_guard.py`, `tests/test_plan_completion.py`, frontend vitest/tsc/lint for the hook, ruff.
