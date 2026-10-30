---
status: draft
issue: 968
spec: spec/2026-10-30-968-plan-session-org-scope.md
coders:   # the user asked for agy as a team member this wave
  1: agy
  2: agy
  3: agy
  4: agy
---

# Plan: scope plan session reads to the caller's org

Paths are relative to `apps/web-server/server/` unless they start with `apps/backend/`
or `tests/`.

Approved decisions (from the spec):
- A session's org, in order of precedence: (a) `PlanSession.org_id`, a payload field
  stamped at intake from the org of the project registered for the session's `repo`;
  (b) a lazy stamp persisted in the new `plan_sessions.org_id` column (write-once, does
  not touch `payload` or `version`; an in-process dict when there is no shared store);
  (c) resolved now from `projects.json` and persisted as (b). A repo that maps to no
  project, to more than one org, or to a matching project without `org_id`, is unknown
  (`None`). Unknown is never persisted. **Schema change:** alembic `e5b8c3f1a9d7`
  adds the nullable column (lead's rule, spec amendment).
- Per-session reads (`get_session`, `audit_pack`): an org-scoped `acw_` key
  (`user["api_key_id"]` and `user["org_id"]` set) on a session that is not known to be
  in its org (another org **or unknown**) is refused always. Human callers get a viewer-in-the-session's-org check, shadow or
  enforce through `RBAC_ENFORCE_TASK_ROUTES`. Org-less sessions are allowed without
  an audit row while the flag is off. A refusal is **404 `unknown session '<id>'`**,
  never 403.
- Lists (`list_sessions`) filter and never 403. Flag off: an org-scoped key sees
  only its own org's sessions, and everyone else sees everything. Flag on: a
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

2. Persisted lazy stamp (lead's rule 2). (Step 1 was amended for rule 3 at commit
   time: `_key_outside_org` replaced `_cross_org_key`, so an unknown org counts as
   outside, and the shadow list predicate for org keys is `org == key_org`.)
   - `server/jobstore/plan_session_models.py` `PlanSessionRow`: add
     `org_id: Mapped[str | None] = mapped_column(String(36), nullable=True)` with a
     one-line comment (#968: lazy org stamp, write-once, outside payload/version).
   - New `server/database/alembic/versions/20261030_e5b8c3f1a9d7_plan_session_org.py`:
     revision `e5b8c3f1a9d7`, down_revision `c7e4a2d9b1f3`. `upgrade`:
     `op.add_column("plan_sessions", sa.Column("org_id", sa.String(36), nullable=True))`.
     `downgrade`: `op.drop_column("plan_sessions", "org_id")`. The docstring follows
     `20260924_d4a7e2b9f1c6_plan_session_lease_version.py` and explains why this is not
     in the payload (`process()` holds a copy and CASes on `version`).
   - `server/jobstore/plan_session_store.py`, next to the emit-lease methods:
     - `stamp_org(session_id, org_id) -> str | None`: in one transaction,
       `update(PlanSessionRow).where(session_id == :id, org_id.is_(None)).values(org_id=:org)`,
       without bumping `version` or `updated_at`. Then select and return the row's
       `org_id`, which is the first writer's value, or None if the row is gone.
     - `org_stamps() -> dict[str, str]`: `select(session_id, org_id).where(org_id.is_not(None))`.
   - `apps/backend/plan/service.py`:
     - Add `stamp_org` and `org_stamps` to the `SessionStore` Protocol, with docstrings.
     - `PlanService.__init__`: `self._org_stamps: dict[str, str] = {}` (the no-store
       fallback; shortcut comment: does not survive a restart, single-process dev only).
     - `def org_stamps(self) -> dict[str, str]`: with a store, `store.org_stamps()`. On
       any exception, log a warning and return `{}`. Without a store, a copy of the dict.
     - `def session_org(self, session, resolve: Callable[[PlanSession], str | None], stamps: dict[str, str] | None = None) -> str | None`:
       return `session.org_id` if set. Otherwise `stamps` (or `self.org_stamps()` when
       it is None) `.get(session.session_id)` if set. Otherwise `org = resolve(session)`:
       if falsy, return None (unknown is never persisted). Else persist: with a store,
       `store.stamp_org(sid, org)` and return its result or `org`. On exception, log a
       warning and return `org`. Without a store, `self._org_stamps.setdefault(sid, org)`.
       A docstring names the precedence and why it is outside the payload.
   → verify by `cd apps/backend && .venv/bin/pytest ../../tests/test_plan_service.py ../../tests/postgres/test_plan_session_store.py ../../tests/test_tenant_scoping.py -q`.
     Run the alembic chain test if one exists (`grep -rl "alembic" ../../tests | head`).

3. Routes and MCP proxies.
   - `routes/plan_pipeline.py`:
     - Import `project_paths` from `server.services`, and `authorize_plan_session` and
       `plan_session_filter` from `.project_authz`. Add `logger = logging.getLogger(__name__)`
       if the module has none.
     - `_projects_for(user) -> dict[str, dict]`: return `project_paths.load_projects()`.
       On any exception: if `user` is an org-scoped key (dict with `api_key_id` and
       `org_id`), raise `HTTPException(503, "Project registry unavailable") from None`.
       Otherwise `logger.warning("plan.projects_unavailable", exc_info=True)` and return `{}`.
     - `_repo_org(repo, projects) -> str | None` (rule 3): `None` for a falsy repo.
       Otherwise `parse_repo_ref(repo)` (from `plan.repo_ref`) gives `(provider, name)`.
       Collect `orgs = {pdata.get("org_id") or None for each project dict with repo == name}`.
       Return the one org if `len(orgs) == 1` and it is truthy, else `None`. A one-line
       comment: none, several orgs, or an org-less match = unknown.
     - `_session_org(session, projects, stamps=None) -> str | None`:
       `SERVICE.session_org(session, lambda s: _repo_org(s.repo, projects), stamps)`.
     - `_user(request)`: `getattr(getattr(request, "state", None), "user", None)`.
     - Ingest: `ingest_text` (`body.repo`), `ingest_from_issue` (`body.repo`) and
       `ingest_upload` (`repo`) pass
       `org_id=_repo_org(<repo>, _projects_for(_user(request)))` to `SERVICE.ingest_*`.
     - `list_sessions(request, include_discarded=False, db: AsyncSession = Depends(get_db))`.
       Use the `= Depends(get_db)` default form (already used by `emit` in this module) on
       all three read routes, so the existing direct calls that pass no `db` still work.
       `user = _user(request)`, `allow = await plan_session_filter(user, db)`. When
       `allow` is not None, load `projects = _projects_for(user)` and
       `stamps = SERVICE.org_stamps()` once, and pass
       `keep=lambda s: allow(_session_org(s, projects, stamps))` to `SERVICE.list_sessions`.
     - `get_session(session_id, request, db=Depends(get_db))`: after the tenant check,
       `await authorize_plan_session(_user(request), session_id, _session_org(session, _projects_for(_user(request))), db)`.
     - `audit_pack(session_id, request: Request, format="json", db=Depends(get_db))`:
       the same check right after `SERVICE.get`, before the pack is built.
   - `mcp_stdio/router.py` (~499–528): in `proxy_list_plan_sessions`,
     `proxy_get_plan_session` and `proxy_plan_audit_pack`, after the existing
     `PlanAccessChecker("viewer").check(...)`, set `request.state.user = _principal(key)`
     with a comment (AuthMiddleware skips /api/mcp-stdio/, so the REST handler's org
     rule needs the key's principal). Then call the handler with `db=db`
     (`plan_audit_pack(session_id, request, format=fmt, db=db)`).
   → verify by `cd apps/backend && .venv/bin/pytest ../../tests/test_tenant_scoping.py ../../tests/test_plan_session_delete.py ../../tests/test_rbac_console_and_mcp.py ../../tests/test_mcp_stdio_proxy.py ../../tests/test_audit_pack.py ../../tests/test_plan_from_issue_route.py -q`

4. Tests: new `tests/test_plan_session_org_scope.py`. Use the `env` pattern in
   `tests/test_rbac_console_and_mcp.py`: a sqlite `OrgMember` DB with orgs A and B,
   `viewer-a` a viewer in A; patch `pa.get_settings`, `pa.audit_service.log_audit_event_bg`
   and `pp.project_paths.load_projects` (`{"pa": {"repo": "acme/a", "org_id": "A"},
   "pb": {"repo": "acme/b", "org_id": "B"}}`). Use a fresh `PlanService` as `pp.SERVICE`,
   as `tests/test_tenant_scoping.py` does, with three sessions: one on repo `acme/a`
   (unstamped payload, derived org A), one stamped `org_id="B"`, and one with no repo
   (unknown). All with the flag **off** unless stated:
   - REST app (`pp.router`, with a middleware that sets `request.state.user` from headers):
     an org-B key principal (`api_key_id`, `org_id="B"`) lists only the B session, and
     gets 404 on the A session and on the unknown session for `GET /{id}` and
     `/{id}/audit-pack`. The B session returns 200.
   - MCP app (`proxy.router`, key dependency overridden as in `mcp_env`, the real plan
     handlers, no stubs): an org-B `AuthenticatedKey` gets the same results on
     `/api/mcp-stdio/plan/sessions[/{id}[/audit-pack]]`. The legacy key sees all three.
   - Human `viewer-a` (no `api_key_id`) on the B session: 200 plus one shadow audit row
     with the flag off, 404 with it on. On the A session: 200 in both modes. On the
     unknown session: 200 with no audit row with the flag off. Under enforce, the list
     for `viewer-a` holds only the A session.
   - Rule 2: read the A session once (it resolves to A and persists). Then re-register
     `acme/a` under org B (patch `load_projects`): the session still resolves to A, so
     the org-B key still gets 404 and `viewer-a` still gets 200. Assert
     `SERVICE.org_stamps()` holds the A stamp.
   - Rule 3, each case with the flag off: (i) the repo matches no project, (ii) `acme/a`
     is in two projects of orgs A and B, (iii) `acme/a` is in one project with no `org_id`.
     `_repo_org` returns None (never the first match). An org-A key gets 404 and
     `viewer-a` gets 200 in shadow mode. Nothing is persisted.
   - In-flight `process()` is not broken by a persisting read (the reason for the
     column). Use a `PlanService` backed by the real `PlanSessionStore` on sqlite (see
     `tests/test_plan_stale_write.py` / `tests/test_plan_concurrency.py` for how they
     build one). Take a versioned copy with `SERVICE.get(sid)`, as `process()` does, then
     call `SERVICE.session_org(...)` so it persists org A, then `SERVICE._save(copy)`.
     That save must not raise `StaleSessionError`, and `org_stamps()` must still hold A.
     This test fails if the stamp is written into the payload.
   - A stamped payload org beats a derived one, and `gitlab:acme/a` resolves to org A.
   - `ingest-text` with `repo="acme/a"` stores `org_id == "A"` on the session.
   - Registry error (patched `load_projects` raises) with an org-B key on a per-session
     read of an unstamped session: 503.
   - The org keys in the tests above use org A where the target session is org-less or
     unknown, so a 404 is not caused by a plain cross-org mismatch.
   - In `tests/postgres/test_plan_session_store.py` (Postgres marker, follow its
     fixtures): `stamp_org` is write-once (the second org is ignored and the first is
     returned), it does not bump `version`, and `org_stamps()` returns it.
   → verify by `cd apps/backend && .venv/bin/pytest ../../tests/test_plan_session_org_scope.py -q`.
     Confirm the cross-org cases fail against `origin/dev`'s routes. Then run ruff and
     `scripts/ratchet_lint.py` exactly as CI runs them, and the full suite on Postgres 16
     (`tests/ apps/web-server/tests/ -m "not slow"` with
     `APP_DISABLE_AUTH=true GRAPHITI_ENABLED=false`, and `tests/postgres/ -m postgres`).

## Deviation (lead's rules, 2026-10-30)

Rule 3 deviation, accepted by the lead: org-scoped keys are denied org-less sessions
too, even in shadow mode (`api_keys` has 0 prod rows; see spec "Amendment").

After the plan was approved, the lead added three rules (see spec, "Amendment"). Stamp
at create; persist a lazy resolution once and never re-resolve it; an ambiguous or
unmapped repo is unknown, which is denied to org keys and stays as today for humans in
shadow mode. Persisting needs a write-once column outside the payload, because
`process()` CASes on `version`. So steps 2 and 4 are new: the old step 2 is now step 3,
and the old step 3 is now step 4 with the rule 2/3 tests.

## Tests

- Step checks above. Expected: all pass. The new file has at least 8 tests.
- Full suite on Postgres 16: 0 failed. Ratchet: PASSED.

## Rollback

1. `git revert` the PR.
2. Schema: `alembic downgrade c7e4a2d9b1f3` drops `plan_sessions.org_id`. That loses only
   the lazy stamps, which re-resolve from `projects.json` if the feature is restored.
   Older code ignores the column, so the downgrade is optional and can follow the revert.
3. Sessions stamped at intake keep `org_id` in their JSON payload. The old `PlanSession`
   ignores unknown keys (pydantic's default `extra="ignore"`), so they load unchanged.
