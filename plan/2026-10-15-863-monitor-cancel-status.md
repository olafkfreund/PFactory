---
status: approved
issue: 863
spec: spec/2026-10-15-863-monitor-cancel-status.md
---

# Plan: finalize a monitored run as failed when the monitor is interrupted

Approved decisions (from the spec):

- New `AgentProcessMonitorMixin._fail_interrupted_run(task_id, project_path, spec_id)`:
  current phase via `self._get_current_phase(task_id)`; pop
  `self._task_log_writers[task_id]`, and if present and `spec_id`,
  `finalize` + `set_phase_status(..., "failed")` on both writers; if
  `spec_id and project_path`,
  `await self._update_plan_status(project_path, spec_id, "failed", task_id, emit_events=False)`.
- Called in `except asyncio.CancelledError` only when the user did not stop the
  task (`task_id not in self._task_stopped`), and in `except Exception` before
  its cleanup pops. Each call wrapped: its own error is logged, never raised.
- The cancel branch keeps swallowing the cancellation (no re-raise).
- No reaper, no reader change, no sweep. Hard kill out of scope.

## Steps

1. `apps/web-server/server/services/agent_process_monitor.py`:
   - Add `async def _fail_interrupted_run(self, task_id: str, project_path: Path | None, spec_id: str | None) -> None`
     on the mixin (next to `_monitor_process`, before it), with a docstring
     citing #863. Use the module's logging style (`logging.getLogger(__name__)`
     inside, as `_monitor_process` does at `:310-312`).
   - `:1023-1025` `except asyncio.CancelledError`: replace the stale comment and
     `pass` with: if `task_id not in self._task_stopped`, call the helper in
     `try/except Exception` that logs a warning with `sanitize_log(task_id)`.
   - `:1026` `except Exception as e`: first thing, the same wrapped call.
   → verify by `ruff check` + `ruff format --check` on the file (no new
   findings vs origin/dev).
   Traps: the mixin declares the host's attributes under `TYPE_CHECKING`
   (`:45-110`); `_get_current_phase` and `_task_log_writers` must be declared
   there if not already (add the stub, matching the others). `spec_id` may be
   None (spec-creation runs); skip the writes then. Don't touch the success
   path.
2. `tests/test_agent_monitor_interrupted.py` (new; reuse the setup style of
   `tests/test_agent_monitor_characterization.py:1-50`: sys.path insert,
   `AgentService`, a fake process):
   - fake proc whose `wait()` awaits an `asyncio.Event` that never fires;
   - spec dir under `tmp_path`; real `TaskLogWriter`s
     (`server/services/task_log_writer.py`, or wherever `TaskLogWriter` lives)
     for worktree and main dir set into `service._task_log_writers[task_id]`,
     with the `coding` phase started (active) and
     `service._task_current_phases[task_id] = TaskPhase.CODING`;
   - `service._update_plan_status = AsyncMock()`; patch `_sync_worktree_files`
     to an `AsyncMock` so the poll loop does nothing;
   - run `_monitor_process(task_id, proc, project_path, spec_id)` as a task,
     yield once, cancel, await it;
   - assert `task_logs.json` coding phase status is `failed`,
     `load_spec_metadata(main_spec_dir)["status"] != "in_progress"`, and
     `_update_plan_status` awaited with `"failed"`;
   - second test: `task_id` in `service._task_stopped` → `_update_plan_status`
     not awaited and the phase stays `active`.
   → verify fails on origin/dev (detached worktree at
   `<scratchpad>/pf-base`, copy the test in, run, delete the copy) and passes
   after.
   Traps: the poll loop uses `asyncio.wait_for(proc.wait(), timeout=3.0)`;
   cancel before the timeout so the test is fast. `load_spec_metadata` lives in
   `server/routes/tasks.py:452`.

Deviation (implementation, from the lead's review):

- Step 1: a local `finished` flag is set where the monitor decides the run's
  outcome (`final_status = ...`). Both branches skip `_fail_interrupted_run`
  once it is set. Without it, an interruption or crash during the awaits
  after the terminal write (auto-continue, `_emit_progress`, notifications,
  rmux reap) rewrote a `completed` run as `failed`.
- Step 2: two more tests, a cancel during the post-completion emission and
  an `_emit_progress` crash after completion; both keep `completed`.

## Tests

    /mnt/code/Source-home/GitHub/PFactory/apps/backend/.venv/bin/pytest tests/test_agent_monitor_interrupted.py tests/test_agent_monitor_characterization.py tests/test_agent_event_hygiene.py -q
    ruff check / ruff format --check on the touched files

Expected: all pass.

## Rollback

Revert the commits. Specs written as `failed` by this path read as
`human_review` / `errors`, the same as a user stop, under the old code too.
