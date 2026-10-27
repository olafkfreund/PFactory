---
status: approved
issue: 939
author: olafkfreund
---

# Intent: org role checks on task and plan-pipeline write routes, shadow mode first

Closes #939. Refs olafkfreund/Factory#312 (hub plan
`plan/2026-10-27-312-fleet-rbac-role-model.md`, slice S2).

## Problem

Task and plan routes have no role or org check. Any authenticated principal,
including an org `viewer` with their own JWT, can start, stop, approve or
merge any task in any org (`routes/execution.py:108`, `:550`,
`routes/tasks.py:1622`, `:3754` and every other task-keyed write route), and
can ingest, process, approve or emit any plan session
(`routes/plan_pipeline.py`, 14 write routes). The key caps from #945 add
nothing while the routes ignore roles.

## Proposed outcome

- Every task-keyed and plan-pipeline write route computes an authorization
  decision (minimum role `member`, AIFactory's rule, post-#1911
  `is_service` only).
- `RBAC_ENFORCE_TASK_ROUTES` (default off) decides whether a denial is
  returned. Off: the decision is logged and audited as a would-deny and the
  response is unchanged. On: denied as AIFactory does.
- The would-deny audit trail shows who would lose access before slice S6
  flips the flag.

## Affected users and systems

PFactory web-server: new `routes/project_authz.py`, `config.py`,
`routes/execution.py`, `routes/tasks.py`, `routes/plan_pipeline.py`,
`services/audit_service.py` (two action names). No callers change while the
flag is off.

## Constraints

- Must not lock out any human or service login. PF projects in
  `projects.json` carry no `org_id`, so enforcing now would deny every human:
  the flag stays off until S6 backfills ownership.
- Shadow mode never changes a response, and fails open on any lookup error.
- No gitops, deploy, MFA, `APP_API_TOKEN` or JWT `role` trust changes.
- Same flag name as TFactory (S1) so gitops flips both together.

## Remaining slices (hub plan, in order)

S3 evidence tooling, S4 admin revoke, S5 PF remote MCP authz (#946), S6
project ownership backfill and flag flip, S7 first access review, S8
enforcement follow-ups.

## Open questions

None (pre-approved for the 2026-10-27 wave).
