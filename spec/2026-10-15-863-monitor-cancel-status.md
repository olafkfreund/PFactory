---
status: draft
issue: 863
intent: intent/2026-10-15-863-monitor-cancel-status.md
---

# Spec: finalize a monitored run as failed when the monitor is interrupted

## Design

One new method on `AgentProcessMonitorMixin`
(`apps/web-server/server/services/agent_process_monitor.py`):

```python
async def _fail_interrupted_run(self, task_id, project_path, spec_id) -> None
```

It does what the normal failure path does at `:800-811` and `:916-933`, for
the phase the task was in:

- `actual_phase = self._get_current_phase(task_id)`;
- pop `self._task_log_writers[task_id]`; if present and `spec_id`, call
  `finalize(spec_id, actual_phase)` and
  `set_phase_status(spec_id, actual_phase, "failed")` on both writers;
- if `spec_id` and `project_path`:
  `await self._update_plan_status(project_path, spec_id, "failed", task_id, emit_events=False)`.

Called from:

- `except asyncio.CancelledError` (`:1023`): only when
  `task_id not in self._task_stopped` (a user stop writes its own state in
  `stop_task`). Wrapped so its own error is logged, not raised. The branch
  keeps swallowing the cancellation, as today. The stale comment is corrected.
- `except Exception` (`:1026`): before the cleanup pops, same call, also
  wrapped.

The status the reader then derives: no active phase, a failed phase →
`human_review` / `errors` (`routes/tasks.py:510-517`). That is the same as a
user stop, so the board already knows how to show it.

It works on a graceful shutdown: the server handles the stop signal, and
`asyncio.run` then cancels pending tasks and runs their handlers; the writes
are local file writes.

## Alternatives rejected

- A startup reaper that flips `active` phases with no running process: needs
  a liveness signal across process lifetimes (and a multi-replica story);
  larger, and the in-process path covers the graceful shutdown the issue
  describes.
- Change `load_spec_metadata` to distrust an `active` phase: it cannot tell a
  live run from a dead one without the same liveness signal.
- Re-raise `CancelledError`: correct in principle but changes the task's end
  state for anything awaiting it; not needed for this fix.

## Risks

- `_update_plan_status` returns early when the plan file is missing
  (`agent_service.py:325`); the `task_logs.json` write alone already moves the
  reader off `in_progress`.
- A write during shutdown that is itself cancelled again: only if a second
  cancel arrives; then behaviour is today's.
- Hard kill: unchanged, out of scope.

## Verification

`tests/test_agent_monitor_interrupted.py`: an `AgentService` with a fake
process whose `wait()` blocks, real `TaskLogWriter`s on a tmp spec dir with an
active `coding` phase, `_update_plan_status` mocked. Start `_monitor_process`
as a task, cancel it, await it: `task_logs.json`'s phase is `failed`,
`load_spec_metadata(spec_dir)["status"]` is not `in_progress`, and
`_update_plan_status` was called with `"failed"`. A second test: with
`task_id` in `_task_stopped`, nothing is written. Fails on `origin/dev`,
passes after. Plus the existing `tests/test_agent_monitor_characterization.py`.
