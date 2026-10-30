---
status: approved
issue: 968
author: olafkfreund
---

# Intent: scope plan session reads to the caller's org

## Problem

Plan sessions carry no organization. The plan reads (REST
`GET /api/plan/sessions`, `GET /api/plan/sessions/{id}`,
`GET /api/plan/sessions/{id}/audit-pack`, and the stdio MCP proxies for the same
three in `apps/web-server/server/mcp_stdio/router.py`) return every session to any
caller that passes `PlanAccessChecker("viewer")`. That checker (added to the MCP
reads in #966) only asks whether the caller is at least a viewer in *some* org (an
`acw_` key: in its own org). It never compares a session to the caller's org. So
an `acw_` key for org B, with viewer in B, can list, read and export org A's plan
sessions. The REST list filters only by tenant, and only when multi-tenancy (#308)
is on.

This must be fixed before S6 turns `RBAC_ENFORCE_TASK_ROUTES` on (Factory#312).

## Proposed outcome

- Every plan session has an effective org: the org it was stamped with at intake,
  or else the `org_id` of the project registered for its target repo.
- An org-scoped `acw_` key never sees another org's session: the lists leave those
  sessions out, and a per-session read or audit-pack export of one is refused. This
  holds with the flag off, as the cross-org key rule in #957/#966 does.
- Human callers get the same check (viewer in the session's org) in shadow mode
  behind `RBAC_ENFORCE_TASK_ROUTES`: logged and audited while the flag is off,
  enforced when it is on.
- Sessions whose project has no `org_id` (all of them today) stay readable as they
  are today while the flag is off.

## Affected users and systems

- PFactory web-server: `routes/plan_pipeline.py`, `routes/project_authz.py`,
  `mcp_stdio/router.py`; backend `plan/service.py` (`PlanSession`).
- MCP clients and the portal cockpit that list and read plan sessions.
- Nothing changes for current traffic: projects carry no `org_id`, PF prod has no
  `acw_` keys, and the legacy `API_TOKEN` is the service principal.

## Constraints

- Must not lock out any human or service login, or change gitops/cluster config.
- Additive: old persisted sessions must still load; a rollback must not need a
  data migration.
- Follow the existing shadow/enforce switch in `project_authz.py`; no second DB
  session in a check path.
- Regression tests: an org-B key cannot list, read or export org A's session, with
  the flag off.

## Open questions

None. The lead chose the design: the org comes from the session's project.
Plan write routes (ingest/process/approve and so on) are out of scope here; they
stay on `PlanAccessChecker("member")`.
