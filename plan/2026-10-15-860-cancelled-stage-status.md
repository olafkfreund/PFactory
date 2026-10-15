---
status: approved
issue: 860
spec: spec/2026-10-15-860-cancelled-stage-status.md
---

# Plan: fail a stage's status when its task is cancelled

Approved decisions (from the spec):

- One helper `fail_status_on_cancel(task, spec_dir, stage, write_patch)` in
  `apps/backend/agents/agent_infra.py`; it adds a done-callback that, on
  `task.cancelled()`, writes `status=<stage>_failed`,
  `phase=<stage>_cancelled`, `<stage>_error="task cancelled"` through the
  module's own `_write_status_patch`.
- Skip when the status is already terminal: ends in `_failed`, or is
  `triaged` / `triaged_empty`.
- Called from all four schedulers. Stage bodies unchanged.
- No liveness change (handoff states stay out of `ACTIVE_STATUSES`), no sweep
  dependency. Hard kills stay out of scope.

## Steps

1. `apps/backend/agents/agent_infra.py` (after `write_status_patch`, ~line
   82): add `fail_status_on_cancel` and a module-level
   `_TERMINAL = frozenset({"triaged", "triaged_empty"})`. The callback reads
   `read_status(spec_dir).get("status")`, returns if terminal, else calls
   `write_patch`. Add it to the module docstring list.
   → verify by `ruff check apps/backend/agents/agent_infra.py`.
   Traps: `Callable` is imported only under `TYPE_CHECKING`; the module uses
   `from __future__ import annotations`, so annotations are fine.
2. Wire the four schedulers, each keeping its return value:
   - `planner.py:951` `schedule_planner`: bind the task, call the helper
     with stage `"planner"` and `_write_status_patch`, return it.
   - `gen_functional.py:610`: stage `"gen_functional"`.
   - `evaluator.py:705-708`: stage `"evaluator"`; the evaluator's
     `_write_status_patch` is imported from its helpers (line 87).
   - `triager.py:920-923`: stage `"triager"`; the local writer fires the
     completion callback on `triager_failed`.
   → verify by `git grep -n fail_status_on_cancel apps/backend` shows 4 calls.
   Traps: schedulers return `None` when their env gate is off; call the helper
   only on a real task.
3. `tests/test_stage_cancel_status.py`: three tests from the spec (planner
   cancelled mid-run, gen-functional cancelled before start from `planned`,
   evaluator already `evaluator_failed` stays). Stub `run_planner` /
   `run_gen_functional` / `run_evaluator` with monkeypatch on the module so
   no LLM runs; set the stage's `PFACTORY_AUTO_*` env to `1`.
   → verify it fails on the unfixed code (`git stash` steps 1-2) and passes
   after.

## Tests

    /mnt/code/Source-home/GitHub/PFactory/apps/backend/.venv/bin/pytest tests/test_stage_cancel_status.py tests/test_planner_integration.py tests/test_liveness.py -q
    ruff check apps/backend/agents tests/test_stage_cancel_status.py
    ruff format --check <touched files>

Expected: all pass.

## Rollback

Revert the commit. No data migration; statuses already written stay valid
`<stage>_failed` values every reader knows.
