---
status: draft
issue: 958
spec: spec/2026-10-30-958-mcp-read-authz.md
coders:   # the user asked for agy as a team member this wave
  1: agy
  2: agy
  3: agy
---

# Plan: project checks on the stdio MCP read proxies and the console SSE stream

Paths are relative to `apps/web-server/server/` unless they start with `tests/`.
Reads need `viewer`. A key whose `org_id` differs from the project's `org_id`
is denied always (403, not behind the flag). Same-org role checks run the
existing shadow/enforce switch (`RBAC_ENFORCE_TASK_ROUTES`, default off = log
`rbac.would_deny` + audit, never change a response). Nothing changes today:
projects carry no `org_id`.

Repo traps (every step):
- CodeQL: no variable first assigned in a `try` and read in an `except`/after
  it; no function mixing implicit and explicit returns (end `-> None` with `return None`).
- No second DB session/connection in a check path: use the route's `db`
  (`Depends(get_db)`); `authorize_project_for_user` already rolls back before it audits.
- Tests patch through the module object the code reads (`proxy.project_paths`,
  `pa.audit_service`, `pa.get_settings`, `proxy.get_db`): other tests reload `server.*`.
- Ruff line length / `# noqa` style as in `project_authz.py`; keep comment density.

## Steps

1. `mcp_stdio/router.py`: helpers and per-task reads.
   - Import `from server.services import project_paths` and
     `authorize_project_for_user` from `server.routes.project_authz`.
   - `def _foreign_projects(key) -> set[str]`: empty unless `key` is an
     `AuthenticatedKey` with an `org_id`; else ids from `project_paths.load_projects()`
     whose `org_id` is set and `!= key.org_id`. A lookup exception: re-raise if
     `project_authz._safe_enforcing()` else return an empty set (shadow fails open).
     Comment: not behind the flag; an org-A key must never read org B.
   - `async def _authorize_read(key, project_id, db) -> None`: if `project_id in
     _foreign_projects(key)`: `await authorize_project_for_user(_principal(key), project_id, db, "viewer")`
     (raises 403 + audit); then `await authorize_project(_principal(key), project_id, db, "viewer")`.
   - `/tasks/{task_id}`, `/status`, `/logs`, `/worktree/diff`: add
     `db: Annotated[AsyncSession, Depends(get_db)]`, name the key `key`, first
     statement `await _authorize_read(key, task_id.split(":", 1)[0], db)`.
   - Lists: `/projects` drops items whose `"id"` is foreign; `/tasks` rebuilds the
     `TaskList` without tasks whose `project_id` is foreign (`total` = new length);
     `/tasks/running` rebuilds `RunningTasksResponse` without ids whose prefix
     before `:` is foreign. Compute `_foreign_projects(key)` once per request.
   - Plan reads `/plan/sessions`, `/plan/sessions/{session_id}`, `/audit-pack`: add
     `db` and `request` where missing, first statement
     `await PlanAccessChecker("viewer").check(_principal(key), request.url.path, db)`.
     `/plan/meta/categories` stays as is (static metadata).
   → verify by `cd apps/backend && PYTHONPATH=. .venv/bin/pytest ../../tests/test_mcp_stdio_proxy.py ../../tests/test_rbac_console_and_mcp.py -q`

2. Console SSE and audit actor.
   - `routes/tasks.py` `@router.get("/{task_id}/agent-console/sse")` (~1902):
     `dependencies=[Depends(require_task_access("viewer"))]`.
   - `rmux/bridge.py` attach (~175) and detach (~236): replace the
     `request.state.user_id` / `org_id` reads with
     `user = getattr(request.state, "user", None) or {}`, `user.get("id")`, `user.get("org_id")`.
   - Add the SSE route to the inventory in `tests/test_task_route_rbac.py` (bump its count).
   → verify by step-1 command plus `../../tests/test_task_route_rbac.py`

3. Tests in `tests/test_rbac_console_and_mcp.py` (reuse `env` / `mcp_env`; extend
   `mcp_env` to override the read routes' key dependency and stub the read handlers):
   - org-B key on `/tasks/p1:s/logs` and `/worktree/diff`: 403 with the flag off,
     handler not awaited, one audit row.
   - same-org viewer key: 200 with no audit; same-org key without membership:
     200 in shadow with a shadow audit, 403 in enforce. Legacy key: 200.
   - `/projects`, `/tasks`, `/tasks/running` with an org-B key return only pB entries;
     legacy key returns all.
   - plan read with a key whose org has no membership: shadow 200 + audit, enforce 403.
   - attach audit row carries `user_id == "member-a"` and the caller's `org_id`.
   → verify by the step-2 command; then ruff, `scripts/ratchet_lint.py` exactly as CI
     runs it, and the full suite on Postgres.

## Tests

`cd apps/backend && PYTHONPATH=. .venv/bin/pytest ../../tests -q` with the Postgres
`DATABASE_URL` CI uses; all pass. Ratchet: `apps/backend/.venv/bin/python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts` clean.

## Rollback

Revert the PR. No schema or config change.
