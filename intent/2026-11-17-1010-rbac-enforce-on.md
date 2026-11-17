---
status: approved
issue: 1010
author: olafkfreund
---

# Intent: turn on RBAC enforcement for task routes (pfactory, tfactory)

## Problem

`APP_RBAC_ENFORCE_TASK_ROUTES` is off, so org confinement of task, plan and project
routes runs in shadow mode only (denials are logged, not applied). Pre-checks in
`docs/dev/rbac-enforce-runbook.md` pass except one: 2 projects in pfactory and 2 in
tfactory (`projects.json`) had no `org_id`. Under enforce, protected task/plan operations on those projects return 403 for
every non-service caller and their plan sessions are hidden.

## Proposed outcome

Every project has an `org_id`, the runbook pre-checks are all 0, and both services run
with `APP_RBAC_ENFORCE_TASK_ROUTES=true`.

## Affected users and systems

pfactory and tfactory pods in namespace `factory` (live), `factory-gitops` manifests.
Logins and API-key callers are affected by the flip.

## Constraints

- No route, CLI or migration sets a project's `org_id` (checked in `routes/projects.py`,
  `services/project_paths.py`, docs), so the backfill is a file edit in the app container.
- Backup first, atomic write, JSON validated. No pod restart for the backfill.
- The flip restarts pods: wait until no release wave is promoting.
- Owner delegation 2026-11-17: use the single org of each service.

## Open questions

None. Decision (owner delegation 2026-11-17): assign each service's single org to its
projects, then enforce. Rejected: leaving projects org-less (hidden under enforce).
