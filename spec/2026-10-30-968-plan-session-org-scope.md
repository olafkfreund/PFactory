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

## Amendment (lead's rules, 2026-10-30)

The lead confirmed the direction (a payload field for the intake stamp) and added
three rules. They replace the matching parts of the design above.

1. **Stamp at create whenever the project's org is known.** This is unchanged: the
   ingest routes pass the org of the repo's project into `PlanSession.org_id`.
2. **A lazy resolution is persisted once and never re-resolved.** The first read
   that resolves an org for an unstamped session writes it. Every later read uses the
   written value, so re-registering a repo under another org cannot move an existing
   session.
   - The value is **not** written into the session payload. `process()` holds one
     session copy for the whole pipeline run and saves it once at the end with a
     compare-and-set on `version`. A read-time payload write would bump `version`, and
     the in-flight run would then fail with `StaleSessionError`.
   - It goes in a new nullable **`plan_sessions.org_id` column**, written with
     `UPDATE ... SET org_id = :org WHERE session_id = :id AND org_id IS NULL`. The
     write does not touch `payload` or `version`, the same way the emit lease columns
     (#758) work. The first writer wins, and the stamp returns the stored value.
   - **This is a schema change.** Alembic revision `e5b8c3f1a9d7` (down revision
     `c7e4a2d9b1f3`) adds the column. Downgrade drops it. Older code ignores it.
   - Without a shared store (no `DATABASE_URL`, single-process dev), the stamp is
     kept in an in-process dict. shortcut: it does not survive a restart, and is fine
     for single-process dev only.
   - Precedence: the payload `org_id` (intake) first, then the persisted lazy stamp,
     then resolve and persist. A store error while stamping is logged, and the
     resolved value is still used for that request.
3. **Org-less versus unknown** (lead's refinement, consistent with #966). The resolver
   (`_repo_org`) looks at every project whose `repo` matches the session's repo, and
   never picks the first match:
   - **An org:** every match carries the same `org_id`. Persisted (rule 2).
   - **Org-less:** every match has no `org_id`, which is normal before S6. Not
     persisted, so the session picks up the project's org once S6 assigns one.
   - **Unknown:** no match, matches that disagree (two orgs, or an org and an org-less
     one), or a session with no `repo`. Not persisted.
   - Access: humans are unchanged. Org-less and unknown sessions are allowed in shadow
     mode without an audit row, and denied under enforce. **An org-scoped `acw_` key is
     denied both, always, with the flag off too:** lists hide them, and per-session
     reads and audit-pack exports get 404. That is #966's unknown-project rule. In
     code, both are `org_id=None`, and an org key passes only when the session's org
     equals the key's org (`_key_outside_org`).

**Deviation from the lead's rule 3 (accepted by the lead, 2026-10-30).** The lead's
rule kept org-less sessions as today for every caller. For org-scoped keys this design
denies them too, even in shadow mode. One reason is fail-closed consistency with #966.
The other is that an org-less session cannot be shown to belong to the key's org.
Evidence that nothing breaks: the lead verified that `api_keys` has 0 rows in all three
prod databases, so no org-scoped key exists today. The consequence is recorded in the
S6 runbook (#959, PR #972): projects need an `org_id` before the first org-scoped key is
minted, or that key sees no plan sessions.

This reverses the earlier "an org key sees org-less sessions in shadow mode" in the
design above.

The "No schema change" and "Alternatives rejected → alembic migration" text above is
superseded for the lazy stamp. The intake stamp still lives in the payload.

Added verification: a persisted lazy stamp survives re-registering the repo under
another org (rule 2). No match, a two-org match and an org-less match are each denied
to an org key with the flag off, and allowed to a human in shadow mode (rule 3). A read
that persists the org during an in-flight `process()` does not break that run: the run
saves without `StaleSessionError`, and the stamp survives. The migration upgrades and
downgrades on Postgres.

## Review revision (lead's Opus review of #976, 2026-10-30)

The read rule was confirmed, with no cross-org read leak. The lead decided these
changes before merge:

1. **An org key ingesting a repo with no known org stamps the key's org.** "No known
   org" means no project, an org-less project, or an ambiguous match. The key can then
   read its own session back. Humans are unchanged: they get the repo's org, or none.
2. **A cross-org ingest is refused.** If the repo's org is known and the caller lacks
   `member` in it:
   - an org-scoped `acw_` key on another org's repo gets **403 always**, audited and
     not behind the flag;
   - a human is logged as `rbac.would_deny` in shadow mode (the session is stamped
     with the repo's org) and refused (403) under enforce;
   - a same-org key without membership follows the flag, as in #966.
   No session ever gets another org's id stamped on it by an org key. The rule is
   `authorize_plan_ingest` in `routes/project_authz.py`. The MCP ingest proxies now set
   `request.state.user = _principal(key)`, as the read proxies do, so a registry error
   gives an org key 503 there too. The upload proxy also passes `repo=None` /
   `base_ref=None` explicitly: called directly, FastAPI does not fill those `Form()`
   defaults.
3. **Performance.** `GET /{id}` and `/audit-pack` look up the stamp by id, with a
   one-row `SELECT` (`PlanSessionStore.org_stamp`), instead of `org_stamps()` over the
   table. They resolve the org (and read `projects.json`) only past the auth-disabled
   and service-principal early returns: `authorize_plan_session` takes a callable.
   Lists still read every stamp once per request.
4. **Under enforce, org-less sessions disappear for humans too.** This is correct as
   designed: shadow-only today. The S6 runbook (`docs/dev/rbac-enforce-runbook.md`)
   now says every project needs an `org_id` before enforce, for humans as well as keys.

Known limits, left as they are (lead's decision):
- Repo matching drops the provider (`gitlab:acme/a` matches a project with `repo:
  acme/a`), because `ensure_tracked_project` stores the bare path. Two providers
  hosting the same path would match each other's projects.
- Org-less and unknown are both `org_id=None` in code. They differ only in
  description: both are denied to org keys, and both keep today's behaviour for
  humans in shadow mode.

Out of scope (the lead is filing an issue): cross-org plan **writes**
(process/approve/emit on another org's session), and the `mcp_rpc` shared-secret path.

Added verification: an org key's ingest of an unregistered repo stamps the key's org
and reads back. A cross-org key ingest gets 403 with the flag off and creates nothing.
A human cross-org ingest is shadowed, then refused under enforce. The MCP ingest gives
503 on a registry error. A human under enforce loses org-less sessions. The cross-org
read assertions fail against `origin/dev`, rather than erroring at fixture setup.
