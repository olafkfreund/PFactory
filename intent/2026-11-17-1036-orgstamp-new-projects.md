---
status: draft
issue: 1036
author: olafkfreund
---

# Intent: stamp the creator's org on new projects

## Problem

RBAC enforcement is on in PFactory and TFactory (#1010, #1035, factory-gitops#361).
Enforcement 403s any non-service caller on a project with no `org_id`
(`project_authz.check_project_access`). The existing projects were backfilled by hand,
but every project created afterwards (`POST /api/projects`, the MCP project-create proxy,
PFactory plan-session `ensure_tracked_project`, TFactory spec-ingest self-registration)
is written without an `org_id`. Org users therefore cannot see or use projects they just
created until someone edits `projects.json`.

## Proposed outcome

A project created by an org-scoped principal carries that principal's org and passes
enforcement for its members. Creation by a principal with no single resolvable org
(service principal, user in several orgs) leaves `org_id` unset (logged for a user in several orgs; the service principal is expected org-less and not logged).

## Affected users and systems

PFactory and TFactory web servers (`routes/projects.py`, `routes/project_authz.py`,
`mcp_stdio/router.py`, PFactory `routes/plan_pipeline.py`, TFactory `routes/specs.py`).
Org users and org-scoped MCP keys. Counterpart: olafkfreund/TFactory#1537.

## Constraints

- No schema change; `org_id` is the existing key in `projects.json`.
- Service callers keep working unchanged (they bypass org checks).
- No single-org default resolver exists in either repo, so none is added.
- Existing org-less projects are not touched here (backfilled by #1010).

## Open questions

None. Delegated decision (owner, 2026-11-17): a user in several orgs gets no stamp
rather than a guess; picking an org explicitly is a later feature.
