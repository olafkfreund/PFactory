---
status: draft
issue: 958
intent: intent/2026-10-30-958-mcp-read-authz.md
---

# Spec: project checks on the stdio MCP read proxies and the console SSE stream

## Design

- `mcp_stdio/router.py`: `_foreign_projects(key) -> set[str]`: project ids whose
  `org_id` is set and differs from an `AuthenticatedKey`'s `org_id`
  (`project_paths.load_projects()`; empty for the legacy key or a key with no
  org; a lookup error fails open in shadow and raises in enforce).
- `_authorize_read(key, project_id, db)`: if the project is foreign, call
  `authorize_project_for_user(..., "viewer")` (always enforces: 403, audit, rollback);
  then `authorize_project(_principal(key), project_id, db, "viewer")` (shadow/enforce).
- Per-task reads (`/tasks/{task_id}`, `/status`, `/logs`, `/worktree/diff`) call
  `_authorize_read` with `task_id.split(":", 1)[0]`.
- Lists (`/projects`, `/tasks`, `/tasks/running`) drop entries in `_foreign_projects(key)`
  instead of 403ing.
- Plan reads (`/plan/sessions`, `/plan/sessions/{id}`, `/audit-pack`):
  `PlanAccessChecker("viewer").check(...)`, like the plan writes use `"member"`.
- `routes/tasks.py` SSE route: `dependencies=[Depends(require_task_access("viewer"))]`.
- `rmux/bridge.py` attach/detach: actor from `request.state.user["id"]` / `["org_id"]`.

## Alternatives rejected

- 403 on lists for a foreign key: the issue asks for filtering, and lists span projects.
- Binding plan sessions to an org: needs a schema change; the org-membership rule is enough for S6.
- Moving the always-on cross-org check into `project_authz`: changes every route; out of scope.

## Risks

Lists hide projects with a foreign `org_id` from org-bound keys; none exist today.

## Verification

New tests in `tests/test_rbac_console_and_mcp.py` (cross-org 403 with the flag off,
viewer shadow audit, list filtering, plan viewer check, SSE dependency, audit actor);
existing `tests/test_mcp_stdio_proxy.py`, `tests/test_task_route_rbac.py`; ruff; ratchet; full suite on Postgres.
