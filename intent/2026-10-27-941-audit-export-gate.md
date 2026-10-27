---
status: approved
issue: 941
author: olafkfreund
---

# Intent: audit-export org gate

Closes #941. Refs olafkfreund/Factory#312.

## Problem

`GET /api/audit/export` (`routes/audit.py::export_audit_logs`) depends only on
`get_current_user`. Any authenticated user, including a human login today, can
stream any org's audit log with `?org_id=X`, or the whole deployment's log by
leaving `org_id` out. AIFactory had the same bug (#1915, fixed in PR #1916).

## Proposed outcome

- With `org_id`, the export needs the `admin` (or `owner`) role in that org,
  read from the `OrgMember` row. An `acw_` key may only export its own org.
- Without `org_id`, it is refused unless the caller is a service principal
  (the legacy API token, or auth-disabled dev).

## Affected users and systems

PFactory web-server `routes/audit.py` and `auth.py` (principal markers). Org
admins and auditors. Callers checked: no code caller of the export in
CFactory, factory-gitops, or this repo's frontend; only docs and the
CHANGELOG mention it (the export is not in `openapi.yaml`). Nobody uses the unscoped form.

## Constraints

No gitops changes. No JWT role claim is trusted. The service principal keeps
its unscoped export. No project/role RBAC on task routes (out of scope).
This repo has no access-review route, so the AIFactory `?org`/`?org_id`
mismatch fix does not apply.

## Open questions

None.
