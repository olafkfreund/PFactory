---
status: approved
issue: 2003  # olafkfreund/AIFactory#2003
author: olafkfreund
---

# Intent: chart creates the alembic Job it promises for autoApply=false

## Problem

`charts/pfactory/values.yaml` (`migrations:` block) says that with
`autoApply: false` "an alembic-upgrade Job is created and the deployment
waits for it". No template creates such a Job and no init container waits.
`autoApply: false` is the chart default and the documented production
setting, so a chart install never migrates and pods fail the
verify-at-head check in `server/database/engine.py`.

Reported as PFactory#2003; PFactory's chart carries the same comment
(re-verified on origin/dev, 2026-10-31). Same fix as AIFactory PR #2004.

## Proposed outcome

With `migrations.autoApply=false`, `helm install`/`helm upgrade` runs
`alembic upgrade head` before the app pods roll, and the values comment
says exactly what the chart does. With `autoApply=true` nothing new renders.

## Affected users and systems

Operators installing the PFactory Helm chart. Prod (factory-gitops plain
manifests, `MIGRATIONS_AUTO_APPLY` default true) is unaffected.

## Constraints

- No change to the default render when `autoApply=true`.
- The Job runs with the app image and the same DB secret as the web Deployment.
- No new values keys beyond the existing `jobImage` / `jobBackoffLimit`.

## Open questions

None. Decision given by the lead: Helm pre-install/pre-upgrade hook Job.
