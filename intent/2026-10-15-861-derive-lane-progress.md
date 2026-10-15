---
status: approved
issue: 861
author: olafkfreund
---

# Intent: lane_progress reads all-pending on a finished or failed task

## Problem

`status.json`'s `lane_progress` is written only as `"pending"`: at task
creation (`apps/backend/agents/tools_pkg/tools/task_control.py:401`) and on a
lane rerun (`:708`). No stage advances it. So a task that reached `triaged`,
or failed at `planner_failed`, still shows every lane as pending.

Readers: MCP `task_status` (`task_control.py:473`) returns the raw
`status.json`, and the portal's `GET /api/pfactory/tasks/{id}`
(`apps/web-server/server/routes/pfactory_tasks.py:213-236`) feeds it to the
Lanes tab (`PFactoryTaskDetail.tsx:647` → `LaneStatusGrid`).

TFactory had the same bug (TFactory#1161) and fixed it there only, by deriving
the field (`agents/lane_progress.py`, TFactory#1259).

## Proposed outcome

`task_status` and the portal show each planned lane's state from the task's
real status: in flight while evaluating, done once evaluation finished, and
failed or empty when the pipeline stopped before the lanes ran. A finished or
failed task is never all-pending.

## Affected users and systems

PFactory MCP `task_status`, the portal task detail and its Lanes tab.

## Constraints

- Works for tasks already on disk (no backfill job).
- Must not need a frontend change if the existing status colours suffice.
- Never raises on unreadable files.

## Open questions

None.
