---
status: approved
issue: 949
author: olafkfreund
---

# Intent: role checks on the task writes #950 could not key on a path

Closes #949. Refs olafkfreund/Factory#312 (S2 follow-up). Same gaps as
TFactory #1458, checked here for PFactory.

## Problem

#950 put `require_task_access` / `require_project_access` /
`require_plan_access` on routes whose project id is in the path. These task
writes still have no org role check:

- `POST /api/tasks` (`routes/tasks.py` `create_task`): project id in the body.
- `POST /api/tasks/create-and-run` (`routes/execution.py`): project id in the query.
- `routes/pfactory_tasks.py` `POST /{spec_id}/merge`, `/{spec_id}/dismiss`,
  `/{spec_id}/visual-baselines/{target}/{snapshot}/accept`: keyed on spec id only.
- `routes/auto_fix.py` `PUT /{projectId}/auto-fix/config`, `POST .../check-new`,
  `POST .../{issueNumber}/start`, and `routes/insights.py` `POST .../create-task`:
  the path param is camelCase `projectId`, so `ProjectAccessChecker` does not fit.
- `mcp_stdio/router.py`: the task and plan write proxies call the handlers
  directly, so no route dependency runs. They are gated only by `acw_` scope;
  a key from org A can act on org B's tasks.
- `rmux/bridge.py`: agent-console attach/detach (attach gives keyboard input
  to the live agent) and the console WebSocket.

And one defect in the #950 code (TFactory #1458 item 1): `_AccessChecker._authorize`
only rolls the request session back on the deny/error paths. On the allow path
the membership query leaves a pooled connection idle in a transaction for the
whole handler (merge and create-pr are long), the AIFactory #1936 class of bug.

## Proposed outcome

Every route above runs the same org role decision as #950, behind
`RBAC_ENFORCE_TASK_ROUTES` (default off = shadow: log `rbac.would_deny`, audit,
never change a response). The request session is released after every check.

## Affected users and systems

PFactory web server only. No behaviour change while the flag is off. No
frontend, gitops or cluster change.

## Constraints

- Shadow by default; enforce only with the existing flag. No login lockout.
- Avoid #950's bugs: one request connection per check (no second pooled
  connection), CodeQL-clean (no uninitialized variable in an except, no mixed
  implicit/explicit returns), tests patch through `project_authz`'s own
  references, not reloaded modules.
- Direct callers of `create_task` (`routes/insights.py`, a test) keep working.

## Open questions

None. Roles: writes need `member`; the console WebSocket (read stream) needs `viewer`.
