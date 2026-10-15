---
status: approved
issue: 860
author: olafkfreund
---

# Intent: a cancelled pipeline stage leaves status.json active forever

## Problem

Each pipeline stage (planner, gen-functional, evaluator, triager) writes its
`<stage>_failed` status only from `except Exception`
(`apps/backend/agents/planner.py:688`, `gen_functional.py:571`,
`evaluator.py:667`, `triager.py:861`). `asyncio.CancelledError` is a
`BaseException`, so a cancelled stage writes nothing and `status.json` keeps
`planning` / `generating` / `evaluating` / `triaging`.

Stages run as fire-and-forget tasks (`schedule_planner`,
`schedule_gen_functional`, `schedule_evaluator`, `schedule_triager`). When the
MCP stdio process exits (`asyncio.run` in `mcp_server/pfactory_server.py:208`
cancels every pending task), a stage that was scheduled but not yet started is
cancelled before its body runs, so the handoff status (`pending`, `planned`,
`generated`, `evaluated`) also stays forever.

The liveness sweep that would flag a stuck active status is off by default
(`web-server/server/config.py:94`) and not enabled in production, and it skips
handoff states on purpose (`agents/liveness.py:43`).

## Proposed outcome

When a stage task is cancelled, before or during its run, `status.json` ends
at that stage's `<stage>_failed` with a phase that says it was cancelled. MCP
`task_status` and the portal then show a terminal failure instead of an
active or handoff state. This must hold with no sweep running.

## Affected users and systems

PFactory backend agents (`apps/backend/agents/`), readers of `status.json`:
MCP `task_status`, portal `routes/pfactory_tasks.py`.

## Constraints

- Must not depend on the liveness sweep.
- Must not overwrite a status a stage already made terminal.
- The triager's terminal write must still fire its completion callback.

## Open questions

- A hard kill (SIGKILL, OOM, SIGTERM with no handler) runs no Python code, so
  no in-process fix covers it; only a reaper can. Out of scope here; noted for
  the lead.
