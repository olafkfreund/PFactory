---
status: draft
issue: 825
author: Olaf Krasicki-Freund
---

# Intent: GET /api/tasks/running reaches the running-tasks handler

## Problem

`server/main.py:300-302` mounts two routers on the same prefix, in this
order:

1. `tasks.router` at `/api/tasks`;
2. `execution.router` at `/api/tasks`.

`tasks.py:1074` declares `GET /{task_id}`, and `execution.py:76` declares
`GET /running`. Starlette tries routes in registration order, and
`/{task_id}` matches the literal `running` first, so the running-tasks
handler is unreachable.

Observed in production on 0.6.25 (2026-09-29):

```
GET /api/tasks/running       -> 400 "Invalid task ID format. Expected 'project_id:spec_id'"
GET /api/tasks/<id>/running  -> 200
GET /api/tasks/<id>/status   -> 200
```

**Who is affected:**
- **REST callers of `/api/tasks/running`:** the endpoint never worked in the
  real app. #805 just made it report runs on every replica, but it still
  cannot be reached.
- **Not affected:** the frontend (it has no caller of this path) and the MCP
  stdio proxy (`mcp_stdio/router.py:145` declares its own `/tasks/running`
  before `/tasks/{task_id}`, and calls the handler in-process).

**Why tests missed it:** unit tests mount `execution.router` on its own, so
they never see the ordering.

This is the only collision between the two routers. The other paths they
share differ by HTTP method, for example `GET` (execution) and `PATCH`
(tasks) on `/{task_id}/status`, and Starlette keeps looking past a method
mismatch. Production confirms it: `/{id}/status` returns 200.

## Proposed outcome

- `GET /api/tasks/running` returns the running-tasks list (200) in the real
  app.
- No other `/api/tasks/...` route changes behaviour.
- An app-level test through `create_app()` guards the ordering, so a later
  router reshuffle cannot silently shadow it again.

## Affected users and systems

- `apps/web-server/server/main.py` (router order).
- A new app-level test in `apps/web-server/tests/`.
- REST API users of `/api/tasks/running`.

## Constraints

- No path or behaviour change for any other route.
- No change to the MCP stdio proxy, which already works.

## Open questions

1. **Fix shape:**
   - (a) mount `execution.router` before `tasks.router` (swap two lines);
   - (b) move `GET /running` into `tasks.router`, ahead of `/{task_id}`.

   **Recommended: (a).** It is the smallest change. Every other shared path
   differs by method, so the swap cannot shadow a tasks route: execution
   declares no single-segment dynamic route. The test pins this.
