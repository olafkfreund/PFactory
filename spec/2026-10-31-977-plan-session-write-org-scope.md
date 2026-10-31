---
status: draft
issue: 977
intent: intent/2026-10-31-977-plan-session-write-org-scope.md
---

# Spec: scope plan-session writes to the session's org

## Design

**Item 1 (chosen, lead decision).** `_guard_tenant(session_id, request)` in
`apps/web-server/server/routes/plan_pipeline.py` already guards process and
suggestions/apply for #308 tenancy. It becomes `async _guard_session(session_id,
request, db)`: the same tenant check, then #976's
`authorize_plan_session(user, session_id, lambda: _session_org(session, user), db,
minimum_role="member")`. A missing session still returns early, so each route's
own handler keeps reporting it. Every session write route calls it first:

process, suggestions/apply, re-gate, approve, access/approve, waive, reject,
discard, `DELETE /{id}`, emit (before `db.close()`), emit-contract.

Routes without `request`/`db` gain them (`db: Annotated[AsyncSession,
Depends(get_db)]`, the B008-free form `delete_session` uses). The rule is
#976's unchanged: an org-scoped `acw_` key on another org's (or an unknown /
org-less) session gets 404 `unknown session '<id>'`, always; a human short of
`member` in the session's org gets a shadow audit row with the flag off and 404
under `APP_RBAC_ENFORCE_TASK_ROUTES`; service principal and auth-disabled are
unchanged. Because the check runs before the write, no state changes and no
body is returned on a denial.

The stdio MCP proxies `proxy_plan_process` and `proxy_plan_approve` in
`mcp_stdio/router.py` set `request.state.user = principal` (AuthMiddleware skips
`/api/mcp-stdio/`, as #976 found) and pass `request`/`db` to the handler.

**Item 2 (lead decision).** `routes/mcp_rpc.py` `_resolve_session` behind the
shared `PFACTORY_MCP_SECRET` is a service principal by design: the secret is
held by the factory services, not by an org. Documented in a code comment on
`_resolve_session` and in `docs/dev/rbac-enforce-runbook.md` (the page that
covers MCP auth and the service principal). No code change. Per-org identity for
this endpoint is deferred to S6 (olafkfreund/Factory#312).

## Alternatives rejected

- A second write-specific helper: the lead asked to reuse #976's helper, and
  `authorize_plan_session` already takes `minimum_role`.
- A route-level `Depends()` guard: the MCP proxies call the handlers directly,
  so dependencies would not run for them.
- Per-org identity for `mcp_rpc` now: needs S6 key/identity work (Factory#312).

## Risks

- A human non-member of the session's org writing under enforce now gets 404:
  intended, flag defaults off.
- Org-scoped keys lose write access to org-less sessions, matching #976's read
  rule; `api_keys` had 0 rows in prod per #976.
- `emit` must run the guard before `db.close()` (#961).

## Verification

New `tests/test_plan_session_write_org_scope.py`, flag off: an org-B key gets
404 on every write route for org A's session, the session is unchanged and the
body does not leak; an org-B key can still process its own session; a human
non-member gets a shadow audit row and is allowed with the flag off; MCP
process/approve proxies give 404 to an org-B key. Existing plan pipeline and
MCP tests stay green; ruff and `scripts/ratchet_lint.py` clean.
