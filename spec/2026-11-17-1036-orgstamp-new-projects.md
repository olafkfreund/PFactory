---
status: approved
issue: 1036
intent: intent/2026-11-17-1036-orgstamp-new-projects.md
---

# Spec: stamp the creator's org on new projects

## Design

One helper in each repo's `routes/project_authz.py`: `creator_org(user, db) -> str | None`.
Order: auth disabled or service principal: None. Org-scoped `acw_` key: the key's `org_id`.
Human: their `OrgMember` org if exactly one. Otherwise (a human in zero or several orgs) None plus a `project.org_unstamped`
warning (user id only). The service principal returns None silently: it is org-less by design.

Callers pass the result into creation:

- `add_project` becomes a thin route that resolves the org and calls the old body, now
  `create_project(project, org_id)`, which writes `org_id` when set. The MCP proxy calls
  `create_project` with the key's `org_id` directly.
- PFactory `ensure_tracked_project(repo, org_id=None)` stamps a new record only, never an
  existing one; the three plan-pipeline ingest routes pass `creator_org(...)`.
- TFactory `ingest_spec` resolves the org only when it self-registers a project from
  `git_url`, and `_clone_and_register_project` stamps it. `request` is optional so direct
  callers and existing tests are unchanged.
- The backend agent tool `task_control.project_create` has no principal and stays unstamped.

## Alternatives rejected

- Default to the sole org when the service has one: no such resolver exists, and it
  would silently assign projects.
- Require an `orgId` in the request body: client change, and trust of a client-supplied org.
- Backfill job: treats the symptom, leaves a window of 403s.

## Risks

A user in several orgs still gets an org-less project (403 until set); logged. A project
created by a service caller stays org-less by design. Signature of `add_project` changes
(callers inside the repo updated; HTTP shape unchanged).

Codex review (2026-11-17): (1) a viewer-only user can create a project stamped with their org:
not an escalation, since project creation was never role-gated and an org-less project is 403
for them anyway; gating creation by role is a separate change, out of scope. (2) the service
principal's missing log line: resolved by the wording above.

## Verification

New `tests/test_project_org_stamp.py` in each repo: org by principal kind; a project made by
an org user carries the org and `authorize_project_for_user` allows it under enforce;
an org-less project is 403 for a non-service user; the clone/tracked paths stamp.
Existing project, plan and MCP-proxy tests stay green.
