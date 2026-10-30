---
status: approved
issue: 968
spec: spec/2026-10-30-968-plan-session-org-scope.md
coders:   # the user asked for agy as a team member this wave
  1: agy
  2: agy
  3: agy
---

# Plan: scope plan session reads to the caller's org

Paths are relative to `apps/web-server/server/` unless they start with `apps/backend/`
or `tests/`.

Approved decisions (from the spec):
- A session's org is `PlanSession.org_id`, a new field in the JSON payload. It is
  stamped at intake from the org of the project registered for the session's `repo`.
  When it is `None`, the org is derived at read time from the project whose `repo`
  matches the session's `repo`. **No alembic migration and no schema change.**
- Per-session reads (`get_session`, `audit_pack`): an org-scoped `acw_` key
  (`user["api_key_id"]` and `user["org_id"]` set) on a session of another org is
  refused always. Human callers get a viewer-in-the-session's-org check, shadow or
  enforce through `RBAC_ENFORCE_TASK_ROUTES`. Org-less sessions are allowed without
  an audit row while the flag is off. A refusal is **404 `unknown session '<id>'`**,
  never 403.
- Lists (`list_sessions`) filter and never 403. Flag off: an org-scoped key sees
  org-less and own-org sessions, and everyone else sees everything. Flag on: a
  session is kept only if the rule allows it.
- Service principal (`is_service`) and auth disabled: always allowed, unfiltered.
- The MCP proxies set `request.state.user = _principal(key)` and pass `db`. They
  keep `PlanAccessChecker("viewer")`.

Repo traps (every step):
- CodeQL: no variable first assigned in a `try` and read after it or in its
  `except`. No function that mixes implicit and explicit returns: end a `-> None`
  function with `return None`.
- No second DB session or connection in a check path. Use the route's `db`.
- Tests patch through the module object the code reads (`pa.get_settings`,
  `pa.audit_service`, `pp.project_paths`, `proxy.get_db`), because other tests reload `server.*`.
- CI runs pytest with `APP_DISABLE_AUTH=true`. Tests of the rule must patch
  `pa.get_settings` to `SimpleNamespace(DISABLE_AUTH=False, RBAC_ENFORCE_TASK_ROUTES=...)`.
- Existing direct calls `pp.list_sessions(_Request(...))` / `pp.get_session(sid, _Request(...))`
  pass a request with no `.state` and no `db`. Read the user with
  `getattr(getattr(request, "state", None), "user", None)`, and touch `db` only when a
  membership lookup is needed (flag on, or the session has an org and the caller is a
  non-service user with an id).
- Ruff line length and the `# noqa` style as in `routes/project_authz.py`.
- Keep the comment density of the surrounding code: one line saying why.

## Steps

1. Model, service and rule.
   - `apps/backend/plan/service.py` `PlanSession` (~line 107, after `tenant_id`): add
     `org_id: str | None = None` with a one-line comment (#968: the session's org,
     stamped at intake from its project; None = derive at read time).
   - `ingest_text` (~894) and `ingest_bytes` (~928): new keyword `org_id: str | None = None`,
     and `session.org_id = org_id or None` next to the `tenant_id` stamp.
   - `list_sessions` (~966): new keyword `keep: Callable[[PlanSession], bool] | None = None`.
     Add `and (keep is None or keep(s))` to the comprehension and document it in the
     docstring. Import `Callable` from `collections.abc` if it is not already imported.
   - `routes/project_authz.py`: add these, and add the public ones to `__all__`.
     - `check_plan_session_access(user, org_id, role, minimum_role="viewer") -> None`,
       the pure rule. No user: 401, as in `check_project_access`. Service principal:
       return. No `org_id`: `_forbidden("Plan session is not assigned to an organization")`.
       `user.get("api_key_id") and user.get("org_id") != org_id`:
       `_forbidden("This API key is scoped to another organization")`. `role is None`:
       `_forbidden("You do not have access to this plan session")`. Then
       `_check_role(role, minimum_role)`.
     - `_cross_org_key(user, org_id) -> bool`: True when `user` is a dict with
       `api_key_id` and `org_id`, `org_id` (the session's) is truthy, and the two differ.
     - `async def _member_roles(user, db) -> dict[str, str]`: `{}` unless `user` is a
       dict with `id` and is not the service principal. Otherwise
       `select(OrgMember.org_id, OrgMember.role).where(OrgMember.user_id == user["id"])`,
       returned as a dict.
     - `async def authorize_plan_session(user, session_id, org_id, db, minimum_role="viewer") -> None`:
       return if `_auth_disabled()` or `is_service_principal(user)`.
       `enforce = _safe_enforcing() or _cross_org_key(user, org_id)`.
       If `not org_id and not enforce`: return (readable as today, no audit).
       `roles = await _member_roles(user, db) if org_id else {}`. Run
       `check_plan_session_access(user, org_id, roles.get(org_id or ""), minimum_role)`.
       On `HTTPException exc`, call `_handle_denial(exc, enforce=enforce, user=user,
       resource_type="plan_session", resource_id=session_id, org_id=org_id,
       member=bool(org_id and org_id in roles), minimum_role=minimum_role, db=db)` inside an
       inner `try`. If that raises an `HTTPException` with status 403, re-raise
       `HTTPException(404, detail=f"unknown session '{session_id}'") from None`. Any
       other status is re-raised unchanged. End with `return None`.
     - `async def plan_session_filter(user, db, minimum_role="viewer") -> Callable[[str | None], bool] | None`:
       `None` if `_auth_disabled()` or `is_service_principal(user)`. If not `_safe_enforcing()`:
       an org-scoped key (`api_key_id` and `org_id`) gets
       `lambda org: not org or org == key_org`, and anyone else gets `None`. Flag on:
       `roles = await _member_roles(user, db)` and return a predicate that is True iff
       `check_plan_session_access(user, org, roles.get(org or ""), minimum_role)` does
       not raise `HTTPException`.
   → verify by `cd apps/backend && .venv/bin/pytest ../../tests/test_plan_service.py ../../tests/test_tenant_scoping.py ../../tests/test_rbac_console_and_mcp.py -q`
     and `.venv/bin/ruff check` on the two files.

2. Routes and MCP proxies.
   - `routes/plan_pipeline.py`:
     - Import `project_paths` from `server.services`, and `authorize_plan_session` and
       `plan_session_filter` from `.project_authz`. Add `logger = logging.getLogger(__name__)`
       if the module has none.
     - `_projects_for(user) -> dict[str, dict]`: return `project_paths.load_projects()`.
       On any exception: if `user` is an org-scoped key (dict with `api_key_id` and
       `org_id`), raise `HTTPException(503, "Project registry unavailable") from None`.
       Otherwise `logger.warning("plan.projects_unavailable", exc_info=True)` and return `{}`.
     - `_repo_org(repo, projects) -> str | None`: `None` for a falsy repo. Otherwise
       `parse_repo_ref(repo)` (from `plan.repo_ref`) gives `(provider, name)`. Return
       `pdata.get("org_id") or None` of the first project dict whose `repo == name`, else `None`.
     - `_session_org(session, projects) -> str | None`: `session.org_id or _repo_org(session.repo, projects)`.
     - `_user(request)`: `getattr(getattr(request, "state", None), "user", None)`.
     - Ingest: `ingest_text` (`body.repo`), `ingest_from_issue` (`body.repo`) and
       `ingest_upload` (`repo`) pass
       `org_id=_repo_org(<repo>, _projects_for(_user(request)))` to `SERVICE.ingest_*`.
     - `list_sessions(request, include_discarded=False, db: AsyncSession = Depends(get_db))`.
       Use the `= Depends(get_db)` default form (already used by `emit` in this module) on
       all three read routes, so the existing direct calls that pass no `db` still work. `user = _user(request)`,
       `allow = await plan_session_filter(user, db)`. When `allow` is not None, load
       `projects = _projects_for(user)` once and pass
       `keep=lambda s: allow(_session_org(s, projects))` to `SERVICE.list_sessions`.
     - `get_session(session_id, request, db=Depends(get_db))`: after the tenant check,
       `await authorize_plan_session(_user(request), session_id, _session_org(session, _projects_for(_user(request))), db)`.
     - `audit_pack(session_id, request: Request, db=Depends(get_db), format="json")`:
       the same check right after `SERVICE.get`, before the pack is built.
   - `mcp_stdio/router.py` (~499–528): in `proxy_list_plan_sessions`,
     `proxy_get_plan_session` and `proxy_plan_audit_pack`, after the existing
     `PlanAccessChecker("viewer").check(...)`, set `request.state.user = _principal(key)`
     with a comment (AuthMiddleware skips /api/mcp-stdio/, so the REST handler's org
     rule needs the key's principal). Then call the handler with `db=db`
     (`plan_audit_pack(session_id, request, db=db, format=fmt)`).
   → verify by `cd apps/backend && .venv/bin/pytest ../../tests/test_tenant_scoping.py ../../tests/test_plan_session_delete.py ../../tests/test_rbac_console_and_mcp.py ../../tests/test_mcp_stdio_proxy.py ../../tests/test_audit_pack.py ../../tests/test_plan_from_issue_route.py -q`

3. Tests: new `tests/test_plan_session_org_scope.py`. Use the `env` pattern in
   `tests/test_rbac_console_and_mcp.py`: a sqlite `OrgMember` DB with orgs A and B,
   `viewer-a` a viewer in A; patch `pa.get_settings`, `pa.audit_service.log_audit_event_bg`
   and `pp.project_paths.load_projects` (`{"pa": {"repo": "acme/a", "org_id": "A"},
   "pb": {"repo": "acme/b", "org_id": "B"}}`). Use a fresh `PlanService` as `pp.SERVICE`,
   as `tests/test_tenant_scoping.py` does, with three sessions: one on repo `acme/a`
   (org-less payload, derived org A), one stamped `org_id="B"`, and one with no repo
   (org-less). All with the flag **off** unless stated:
   - REST app (`pp.router`, with a middleware that sets `request.state.user` from headers):
     an org-B key principal (`api_key_id`, `org_id="B"`) lists only the B and org-less
     sessions, and gets 404 on the A session's `GET /{id}` and `/{id}/audit-pack`.
     The B session returns 200.
   - MCP app (`proxy.router`, key dependency overridden as in `mcp_env`, the real plan
     handlers, no stubs): an org-B `AuthenticatedKey` gets the same results on
     `/api/mcp-stdio/plan/sessions[/{id}[/audit-pack]]`. The legacy key sees all three.
   - Human `viewer-a` (no `api_key_id`) on the B session: 200 plus one shadow audit row
     with the flag off, 404 with it on. On the A session: 200 in both modes.
     Under enforce, the list for `viewer-a` holds only the A session.
   - `_session_org`: a stamped org beats a derived one, and `gitlab:acme/a` resolves
     to org A when the project's `repo` is `acme/a`.
   - `ingest-text` with `repo="acme/a"` stores `org_id == "A"` on the session.
   - Registry error (patched `load_projects` raises) with an org-B key on a per-session
     read: 503.
   → verify by `cd apps/backend && .venv/bin/pytest ../../tests/test_plan_session_org_scope.py -q`.
     Confirm the cross-org cases fail on `origin/dev`'s routes by running the new file
     with step 2 stashed into a temporary copy, never with `git stash` in the main clone.
     Then run ruff and `scripts/ratchet_lint.py` exactly as CI runs them, and the full
     suite on Postgres 16 (`tests/ apps/web-server/tests/ -m "not slow"` with
     `APP_DISABLE_AUTH=true GRAPHITI_ENABLED=false`, and `tests/postgres/ -m postgres`).

## Tests

- Step checks above. Expected: all pass. The new file has at least 8 tests.
- Full suite on Postgres 16: 0 failed. Ratchet: PASSED.

## Rollback

`git revert` the PR. No migration, so there is nothing to downgrade. Sessions
stamped with `org_id` keep that key in their JSON payload, and the old
`PlanSession` ignores unknown keys (pydantic's default `extra="ignore"`), so they
load unchanged.
