---
status: approved
issue: 968
intent: intent/2026-10-30-968-plan-session-org-scope.md
---

# Spec: scope plan session reads to the caller's org

## Design

### A session's org

- `PlanSession` (`apps/backend/plan/service.py`) gets `org_id: str | None = None`.
  It lives in the JSON `payload` the `plan_sessions` table already stores, the same
  way `tenant_id` lives in the model. Old payloads load with `None`. **No schema
  change and no alembic migration.**
- **Stamped at intake.** `ingest_text` / `ingest_bytes` take `org_id: str | None = None`
  and set it, like `tenant_id`. The three REST ingest routes in
  `routes/plan_pipeline.py` (`/ingest-text`, `/from-issue`, `/ingest`) pass the
  `org_id` of the project registered for the request's `repo`. The MCP ingest
  proxies call these routes, so they are covered too.
- **Backfill is lazy, at read time.** `_session_org(session)` returns
  `session.org_id`, or else the `org_id` of the project whose `repo` matches the
  session's `repo` (provider prefix stripped with `plan.repo_ref.parse_repo_ref`,
  the same normalization `ensure_tracked_project` uses when it registers the
  project). That gives the lead's "backfill existing sessions from their project"
  without a migration, for two reasons:
  1. Projects live in `projects.json` (`services/project_paths.load_projects()`),
     not in the database, so an alembic migration cannot read them.
  2. Projects get their `org_id` in S6. A one-shot backfill today would find no
     org for any session, and sessions would need a second backfill after S6.
     Deriving at read time picks the org up as soon as the project gets one.
  A stored org wins over the derived one, so re-registering a repo later cannot
  move a session that was stamped at intake.

### The access rule

New in `routes/project_authz.py`, built on the existing pieces
(`is_service_principal`, `_check_role`, `_handle_denial`, `_safe_enforcing`,
`_auth_disabled`):

- `check_plan_session_access(user, org_id, role, minimum_role)` is the pure rule,
  the same as `check_project_access` but for a session's org: no user → 401;
  service principal → allow; no `org_id` → 403 "not assigned to an organization";
  `acw_` key with another `org_id` → 403; no membership → 403; role below minimum → 403.
- **Per-session reads** (`get_session`, `audit_pack`):
  `authorize_plan_session(user, session_id, org_id, db)`.
  - Auth disabled: allow.
  - The decision is enforced when the flag is on **or** when the caller is an
    org-scoped `acw_` key and the session has a different org. That second case is
    not behind the flag, like the cross-org key check in #966.
  - Org-less session while not enforcing: allow without an audit row, as today.
    Every session is org-less today, and the cockpit polls these routes, so a
    shadow audit per read would flood the audit chain with no signal.
  - Otherwise the rule runs. A denial goes through `_handle_denial`: in shadow it is
    logged as `rbac.would_deny` and audited; when enforced it is audited and raised.
    **A 403 is turned into 404 `unknown session '<id>'`**, the existing #308 tenant
    convention in this module: session ids are `NNN-<title-slug>`, so a 403 would
    confirm that another org's session exists and leak its title.
- **Lists** (`list_sessions`): `plan_session_filter(user, db)` returns `None`
  (show all) or a predicate on the session's org. Lists filter and never 403.
  - Auth disabled or service principal: `None`.
  - Flag off: an org-scoped `acw_` key keeps org-less sessions and its own org's
    sessions. Everyone else gets `None`, which is unchanged.
  - Flag on: keep a session only if `check_plan_session_access` allows it. Org-less
    sessions are hidden from non-service callers, the same as projects with no org.
  - `PlanService.list_sessions` takes `keep: Callable[[PlanSession], bool] | None`,
    applied next to the tenant filter. The summary dicts do not carry `repo`, so
    the filter has to run on the sessions themselves.
- Membership is read once per request (`OrgMember.org_id, role` for the user) on the
  route's own `db`. No second session.

### Wiring

- REST handlers `list_sessions`, `get_session` and `audit_pack` take
  `db: Annotated[AsyncSession, Depends(get_db)]` (`audit_pack` also takes `request`)
  and read the caller from `request.state.user`.
- The stdio MCP proxies keep `PlanAccessChecker("viewer")` (#966). `AuthMiddleware`
  skips `/api/mcp-stdio/`, so `request.state.user` is `None` there. Each proxy sets
  `request.state.user = _principal(key)` before it calls the REST handler with
  `db=db`. One rule then serves REST and MCP.

## Alternatives rejected

- **An alembic migration adding an `org_id` column plus a backfill.** The backfill
  source (`projects.json`) is not in the database, and the projects have no org
  yet, so the migration would fill nothing. A column is also not needed: lists
  already load full sessions in Python.
- **Stamp the caller's (key's or user's) org at intake**, the first option in the
  issue. That binds a session to whoever ingested it rather than to the repo it
  changes. The lead chose the project's org.
- **Return 403 on another org's session** (the #966 key check). Rejected for plan
  sessions because the id carries the plan title. 404 matches the tenant guard here.
- **Filter lists in shadow mode for humans too.** That would change responses while
  the flag is off, which the shadow contract forbids.

## Risks

- Once S6 assigns project orgs, an org-scoped key loses sight of sessions for other
  orgs' repos. That is intended. A session whose repo is registered under no project,
  or under an org-less one, stays visible in shadow mode and is hidden under enforce.
- `load_projects()` reads `projects.json` once for each session whose org is not
  stored. Lists are small (the board is in the tens). It is read once per list request.
- A `projects.json` read error on a per-session read: in shadow it fails open,
  matching `_AccessChecker`. For an org-scoped key it fails closed (503), matching
  `_key_projects` in #966.
- Rollback: old code ignores the extra `org_id` key in the payload (pydantic's
  default ignores unknown keys), so a revert needs no data change.

## Verification

- New tests in `tests/test_plan_session_org_scope.py`, all with the flag **off**:
  an org-B `acw_` key cannot list, read or export an org-A session, over REST and
  over MCP. The same key sees org-less and org-B sessions. The legacy/service key
  sees everything.
- Per-session reads of an org-A session by a human without membership: 200 plus a
  shadow audit row with the flag off, 404 with it on.
- Org derivation: stamped beats derived, and a `gitlab:` prefix is stripped.
  Ingest stamps the project's org.
- Existing tests unchanged: `tests/test_tenant_scoping.py`,
  `tests/test_plan_session_delete.py`, `tests/test_rbac_console_and_mcp.py`,
  `tests/test_mcp_stdio_proxy.py`.
- Ruff + the mypy ratchet as in CI. The full suite on Postgres 16.
