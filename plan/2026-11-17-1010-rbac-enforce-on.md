---
status: approved
issue: 1010
spec: spec/2026-11-17-1010-rbac-enforce-on.md
coders:
  2: lead
reviewers:
  - codex
---

# Plan: backfill project org_id, then enforce

Approved decisions: backfill via container file edit (no API exists), single org per
service, then enforce via factory-gitops env var; rollback = "false".

## Steps

1. (done, live) Backfill `projects.json` in pfactory and tfactory containers with backup
   `projects.json.bak-2026-11-17` and atomic write → verify: 0 projects without org_id.
   pfactory org `ce8416ae-ca10-45c8-8176-2713e883086f`: projects `7c4e7f6a-579f-4391-8a61-b274cb03cd7b`,
   `olafkfreund-pfactory-friends-demo`. tfactory org `897f7dc7-0f0e-4882-917e-3cf3553a55fa`: projects
   `62818c7a-abfb-4ac0-8159-8186d2e7af5e`, `bffa70f5-f437-4d45-8865-e5272e1daa9e`.
   Traps: do not print tokens; do not restart pods.
2. factory-gitops `apps/pfactory/manifests/manifests.yaml` and `apps/tfactory/manifests/manifests.yaml`:
   add the env var before the OIDC block → verify `kubectl kustomize apps/<app>/manifests | grep RBAC_ENFORCE`.
   Traps: open the PR only when no dev->main PR or release run is active.

## Tests

After the flip: watch `authz.denied` audit rows and pod logs for the first hour.

Runbook SQL (steps 1 and 4) return 0 for both databases; kustomize renders the env var.

## Rollback

Set the value to "false" in factory-gitops. Restore `projects.json.bak-2026-11-17` if needed.
