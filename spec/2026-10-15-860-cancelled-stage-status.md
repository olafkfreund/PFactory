---
status: draft
issue: 860
intent: intent/2026-10-15-860-cancelled-stage-status.md
---

# Spec: fail a stage's status when its task is cancelled

## Design

One shared helper in `apps/backend/agents/agent_infra.py`:

```python
def fail_status_on_cancel(task, spec_dir, stage, write_patch) -> None
```

It adds a done-callback to the scheduled task. When `task.cancelled()` is
true and the current `status.json` status is not already terminal (ends in
`_failed`, or is `triaged` / `triaged_empty`), it calls
`write_patch(spec_dir, status=f"{stage}_failed", phase=f"{stage}_cancelled",
**{f"{stage}_error": "task cancelled"})`.

Each of the four schedulers calls it on the task it creates:
`schedule_planner` (`planner.py:938`), `schedule_gen_functional`
(`gen_functional.py:593`), `schedule_evaluator` (`evaluator.py:688`),
`schedule_triager` (`triager.py:907`), passing the module's own
`_write_status_patch`. The triager's local writer fires the completion
callback on `triager_failed` (`triager.py:376-389`), so that is kept.

Why a done-callback and not `except CancelledError` in each stage body: the
callback fires both when the stage is cancelled mid-run and when it is
cancelled before its first step (the handoff case). An `except` in the body
never runs for a task cancelled before it starts. One helper, four one-line
calls; the stage bodies are unchanged.

Done-callbacks run on `asyncio.run` shutdown: `_cancel_all_tasks` waits on a
`gather` of the cancelled tasks, and each task's callbacks are scheduled when
it finishes, ahead of the loop stopping.

## Alternatives rejected

- `except asyncio.CancelledError` in each of the four stage bodies: misses
  the cancelled-before-start handoff case, and is four copies.
- Add the handoff states to `ACTIVE_STATUSES` (`liveness.py:43`), as the
  issue suggests: the sweep is off in production, so it fixes nothing there,
  and with auto-fire off a handoff state is a legitimate resting state that
  the sweep would wrongly flip to `stalled`. Not done.
- Enable the sweep: a deploy change, out of scope, and the brief forbids
  depending on it.

## Risks

- A cancelled task whose stage already wrote a terminal status: guarded by the
  terminal check, so it is never overwritten.
- A write error inside a done-callback is logged by the event loop, not
  raised; the stage is no worse off than today.
- A hard kill (SIGKILL, OOM, SIGTERM with no handler) still leaves the status
  in place. Needs a reaper; flagged, not fixed here.

## Verification

New test in `tests/test_stage_cancel_status.py`:

1. Schedule the planner with a `run_planner` stub that blocks, cancel the task:
   `status.json` reads `planner_failed`, phase `planner_cancelled`.
2. Schedule gen-functional and cancel it before it starts (status `planned`):
   status reads `gen_functional_failed`.
3. A task cancelled after the status is already `evaluator_failed` keeps it.

Fails on `origin/dev` (status stays `planning` / `planned`), passes after.
Plus the existing scheduler tests and ruff.
