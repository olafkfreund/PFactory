---
status: approved
issue: 958
author: olafkfreund
---

# Intent: project checks on the stdio MCP read proxies and the console SSE stream

## Problem

The stdio MCP read proxies in `apps/web-server/server/mcp_stdio/router.py`
(task, status, logs, worktree diff, project/task/running lists, plan session
reads, audit pack) are gated only by the `acw_` `mcp:read` scope. They call
the REST handlers directly, so no project check runs: an `acw_` key bound to
org B can read an org-A project's logs and diffs.

The same gaps TFactory #1466 found exist here: `GET /api/tasks/{task_id}/agent-console/sse`
(`routes/tasks.py`) has no project check, and the rmux console audit rows read
`request.state.user_id` / `org_id`, which no middleware sets, so they record no actor.

## Proposed outcome

- A key for another org gets 403 on per-task reads, always (not behind the flag),
  and never sees another org's projects, tasks or running tasks in list reads.
- Same-org reads run the `viewer` role check in shadow/enforce mode
  (`RBAC_ENFORCE_TASK_ROUTES`), like every other task route.
- Plan reads run `PlanAccessChecker("viewer")`.
- The SSE route carries `require_task_access("viewer")`.
- `console.attach` / `console.detach` audit rows carry the caller's id and org.

## Affected users and systems

PFactory web-server: MCP stdio clients, portal console viewers. Nothing changes
today: projects carry no `org_id` yet, and the flag is off.

## Constraints

- Must not lock out the legacy admin token or same-org keys.
- No second pooled connection per check (#1936); CodeQL-clean; ratchet clean.

## Open questions

None. Remaining Factory#312 work (S6, flag on) is out of scope.
