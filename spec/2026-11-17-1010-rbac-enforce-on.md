---
status: draft
issue: 1010
intent: intent/2026-11-17-1010-rbac-enforce-on.md
---

# Spec: backfill project org_id, then enforce

## Design

1. Backfill: in each app container, copy `projects.json` to `projects.json.bak-2026-11-17`,
   set `org_id` on projects lacking it to the service's only org (read from its own
   `organizations` table), write via temp file + `os.replace`, re-read to validate.
   `load_projects()` reads the file on each call, so no restart is needed.
2. Re-run runbook steps 1 and 4 SQL: all counts 0.
3. After the release wave ends, one factory-gitops PR adds
   `{ name: APP_RBAC_ENFORCE_TASK_ROUTES, value: "true" }` to apps/pfactory and apps/tfactory.

## Alternatives rejected

- Add an admin route/CLI for org assignment: more code for a one-off with 4 rows.
- Keep shadow mode: leaves org confinement unenforced.

## Risks

Enforce can 403 callers (legacy keys, org-less projects): both pre-checks are 0.
Rollback is the flag set to "false". The flip rolls the pods.
Known gap (Codex review, confirmed in `routes/project_authz.py:103-105`): `add_project()` and
`ensure_tracked_project()` still create projects without `org_id`, so a project created
after the flip is 403 for non-service callers until an org is set (same file edit). A
code fix (stamp the creator's org at creation) is a separate follow-up, not in this change.
Zero runbook counts cover key/user-less denials only; a member with too low a role still
gets 403 as designed.

## Verification

Before/after counts of projects without `org_id` (2 -> 0 each); runbook SQL counts 0;
`kubectl kustomize` shows the env var once per app; after sync, pod env shows `true`
and logs show no unexpected `authz.denied`.
