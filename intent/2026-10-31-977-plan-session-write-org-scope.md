---
status: approved
issue: 977
author: olafkfreund
---

# Intent: scope plan-session writes to the session's org

## Problem

#976 (#968) scoped plan-session reads to the caller's org. The write routes in
`apps/web-server/server/routes/plan_pipeline.py` (process, suggestions/apply,
re-gate, approve, access/approve, waive, reject, discard, delete, emit,
emit-contract) and their stdio MCP proxies (process, approve in
`mcp_stdio/router.py`) still only check `PlanAccessChecker("member")` in the
caller's own org. An org-B member key can process or approve org A's session,
change it, and read the whole session back in the response.

Separately, `routes/mcp_rpc.py` `_resolve_session` reads any session behind the
shared `PFACTORY_MCP_SECRET`, with no org.

## Proposed outcome

- Every plan-session write route gives 404 `unknown session '<id>'` when the
  session is in another org, using #976's rule: always for org-scoped `acw_`
  keys, shadow mode for humans behind `APP_RBAC_ENFORCE_TASK_ROUTES`.
- The MCP proxies follow the same rule.
- The `mcp_rpc` shared secret is documented as a service principal by design.

## Affected users and systems

PFactory web-server: plan pipeline routes, stdio MCP proxies, MCP JSON-RPC docs.
Org-scoped keys (0 rows in prod per #976) and humans in shadow mode.

## Constraints

- Reuse #976's `authorize_plan_session`; no second helper.
- Must not lock out the service principal, legacy admin token or humans with the flag off.
- No change to `mcp_rpc` behaviour.

## Open questions

None. Lead decision: item 2 is a service principal by design; per-org identity
is deferred to S6 (olafkfreund/Factory#312).
