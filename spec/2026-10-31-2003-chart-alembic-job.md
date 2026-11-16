---
status: approved
issue: 2003  # olafkfreund/AIFactory#2003
intent: intent/2026-10-31-2003-chart-alembic-job.md
---

# Spec: chart creates the alembic Job it promises for autoApply=false

## Design (chosen, per the lead's decision)

New template `charts/pfactory/templates/job-migrations.yaml`, rendered only
when `migrations.autoApply` is false: a `batch/v1` Job annotated
`helm.sh/hook: pre-install,pre-upgrade`, weight `-5` (after the CNI probe at
`-10`), delete policy `before-hook-creation,hook-succeeded`.

- Image: `migrations.jobImage`, else `pfactory.image` (the web image).
- Command: `/home/projects/MagesticAI/.venv/bin/python -m alembic upgrade head`;
  the image WORKDIR is `apps/web-server`, where `alembic.ini` lives.
- Env: `DATABASE_URL` from the same `secretKeyRef` as the Deployment
  (`postgres.externalSecretName`, key `database-url`) plus
  `PYTHONDONTWRITEBYTECODE=1`. `alembic/env.py` reads only `DATABASE_URL`
  (checked: `alembic upgrade head` runs to head with an otherwise empty env).
  The ConfigMap is a release resource that does not exist at pre-install, so
  it is not mounted.
- Pod: `pfactory.selectorLabels` + `component: migrations` (keeps the pod inside
  the NetworkPolicy; the Service also requires `component: server`, so the pod
  never becomes an endpoint; this chart has no jobPodLabels helper), same pull secrets, pod/container security
  contexts, nodeSelector/tolerations; `/tmp` emptyDir because the root FS is
  read-only. `serviceAccountName` is not set and
  `automountServiceAccountToken: false`: the chart's ServiceAccount does not
  exist yet at pre-install, and the Job needs no API access.
- `backoffLimit: migrations.jobBackoffLimit`, `restartPolicy: Never`.
- Values comment rewritten: the hook runs before pods roll (no init
  container); the DB Secret must exist before the first install.
  README row updated to match.

## Alternatives rejected

- Correct the comment and default `autoApply: true` (issue option 2):
  the lead chose the Job; flipping the default changes prod-mode semantics.
- `post-install` hook: with `helm install --wait`, Helm waits for pods to be
  ready before post-install hooks run, and pods are not ready until the
  schema is at head: deadlock.
- Init container in the Deployment: every replica would race on migrations.

## Risks

- First install with an ExternalSecret-managed or bundled-CNPG DB secret: the
  Secret is created by the release, after pre-install hooks, so the Job pod
  waits on a missing Secret until Helm times out. Documented in values:
  pre-create the Secret or install once with `autoApply=true`.
- A failed migration fails the upgrade; old pods keep running (intended).

## Verification

- `helm lint charts/pfactory`.
- `helm template` with defaults renders the Job with hook annotations;
  with `--set migrations.autoApply=true` renders no Job.
- New `tests/helm/test_migrations_job.py` covering both cases plus
  `jobImage` override; fails on origin/dev (no Job).
