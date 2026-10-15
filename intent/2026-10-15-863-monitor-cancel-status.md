---
status: draft
issue: 863
author: olafkfreund
---

# Intent: a Kanban card stays in_progress when the web-server restarts mid-run

## Problem

`_monitor_process` (`apps/web-server/server/services/agent_process_monitor.py:295`)
writes a run's terminal state at the end: it finalizes the task log writers and
sets the phase status (`:801-811`), then calls `_update_plan_status`
(`:916-933`). When the web-server shuts down mid-run, the monitor task is
cancelled and `except asyncio.CancelledError: pass` (`:1023-1025`) skips both
writes. The `except Exception` branch (`:1026-1046`) skips them too; it only
sends one websocket FAILED event, which is not persisted.

So `task_logs.json` keeps the phase `active`. The reader `load_spec_metadata`
(`apps/web-server/server/routes/tasks.py:497-509`) lets an active phase win,
and `/api/tasks` shows the card `in_progress` forever. There is no startup
reaper, and `fix-stuck-tasks.py` does not repair this state.

The comment on the cancel branch says "cleanup already handled by stop_task",
but `stop_task` (`agent_service.py:1128`) never cancels the monitor: it marks
`_task_stopped` and the monitor returns early on its own (`:791-797`). A
cancellation therefore only comes from shutdown.

## Proposed outcome

When the monitor is cancelled or crashes mid-run (and the user did not stop
the task), the current phase is finalized as `failed` in `task_logs.json` and
the plan status is set to `failed`. After a restart `/api/tasks` shows the card
needing review (`human_review`, reason `errors`), not `in_progress`.

## Affected users and systems

PFactory web-server agent runs and the Kanban board (`/api/tasks`).

## Constraints

- Must not depend on a liveness sweep or startup reaper.
- A user stop keeps its own path (`stop_task`); no double write.
- A hard kill (SIGKILL, OOM) runs no Python and stays out of scope.

## Open questions

- The agent subprocess is not terminated on shutdown; whether it should be is
  a separate question. Out of scope.
