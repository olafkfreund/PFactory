---
status: approved
issue: 861
spec: spec/2026-10-15-861-derive-lane-progress.md
---

# Plan: derive lane_progress from the task status on read

Approved decisions (from the spec):

- New `apps/backend/agents/lane_progress.py`:
  `derive_lane_progress(spec_dir: Path, status: dict) -> dict[str, str] | None`.
- Lanes set: lanes named by `test_plan.json` subtasks (`phases[].subtasks[].lane`,
  stripped, lower-cased); if the plan is missing or unreadable, the keys of the
  stored `status["lane_progress"]`. Others keep the stored value.
- Value per task status:
  - `evaluating` → `evaluating`
  - `evaluated`, `triaging`, `triaged`, `triaged_empty`, `triager_failed` → `evaluated`
  - `planner_failed`, `gen_functional_failed`, `evaluator_failed`, `stalled`,
    `planned_empty`, `generated_empty`, `evaluated_empty` → the status itself
  - anything else → return None (stored value kept)
- Returns the stored dict updated with the derived lanes (a new dict), or None
  when no lane is set. Never raises; never writes `status.json`.
- Applied on read in MCP `task_status` and portal `get_task`. No frontend change.

## Steps

1. Create `apps/backend/agents/lane_progress.py` with the function above and a
   short module docstring citing #861 and TFactory#1259.
   → verify by `ruff check` + `ruff format --check` on it.
   Traps: plan JSON may be a non-dict, phases/subtasks may hold non-dicts;
   `lane` may be missing. Catch `OSError`/`ValueError` on read.
2. `apps/backend/agents/tools_pkg/tools/task_control.py:473-479` (`task_status`):
   after `_load_status`, compute
   `derive_lane_progress(_status_file(project_id, spec_id).parent, status)`;
   when not None set `status["lane_progress"]`. Lazy-import inside the
   function like the file's other agent imports (`from agents.planner import
   schedule_planner` at :414).
   `apps/web-server/server/routes/pfactory_tasks.py:229` (`get_task`): same on
   `status_doc` with `spec_dir`; import `from agents.lane_progress import
   derive_lane_progress` lazily as at :791.
   → verify by `git grep -n derive_lane_progress apps` shows 2 call sites.
   Traps: don't touch `_summary_row` (list view has no lanes); the rerun
   path at :708 keeps writing `pending` (status goes back to `pending`, so
   derivation returns None and the reset shows).
3. Tests:
   - `tests/test_lane_progress.py`: unit cases from the spec (triaged with plan
     naming unit+api; planner_failed with no plan → all lanes planner_failed;
     planning → None; malformed plan → falls back to stored keys).
   - `tests/test_pfactory_mcp_tools.py`: `task_status` on a task whose
     status.json is `triaged` with a plan naming `unit` → `unit` is
     `evaluated` (not pending).
   - `tests/test_pfactory_routes_tasks.py`: `get_task` same case via
     `_make_task(..., status="triaged")` + a `test_plan.json`.
   → verify the two wiring tests fail on `origin/dev` (copy into a detached
   worktree of origin/dev) and all pass after.

Deviations (implementation):

- Step 2: `derive_lane_progress` is imported at module top in both readers,
  not lazily. The repo's lint ratchet blocks a net-new PLC0415, and
  `agents.lane_progress` has no heavy or circular imports.
- Step 1 (from review): old v0.1 lane names in `test_plan.json`
  (`functional`, `sast`, `dast`, `fuzz`) map to `unit` through
  `test_plan.enums._V01_LANE_ALIASES`, as the rest of the code does; otherwise
  the grid's Unit card stayed pending on such plans. Test added.

## Tests

    /mnt/code/Source-home/GitHub/PFactory/apps/backend/.venv/bin/pytest tests/test_lane_progress.py tests/test_pfactory_mcp_tools.py tests/test_pfactory_routes_tasks.py -q
    ruff check / ruff format --check on touched files (no new findings vs origin/dev)

## Rollback

Revert the commits. Nothing is written to disk, so no data to undo.
