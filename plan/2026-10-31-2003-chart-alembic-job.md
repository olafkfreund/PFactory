---
status: approved
issue: 2003  # olafkfreund/AIFactory#2003
spec: spec/2026-10-31-2003-chart-alembic-job.md
---

# Plan: chart creates the alembic Job it promises for autoApply=false

Approved decisions (from the spec):
- New Helm hook Job `charts/pfactory/templates/job-migrations.yaml`, rendered
  only when `.Values.migrations.autoApply` is false.
  Annotations: `helm.sh/hook: pre-install,pre-upgrade`, `helm.sh/hook-weight: "-5"`,
  `helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded`.
- Image `.Values.migrations.jobImage | default (include "pfactory.image" .)`,
  `imagePullPolicy: .Values.image.pullPolicy`, same pull secrets as the Deployment.
- Command `["/home/projects/MagesticAI/.venv/bin/python", "-m", "alembic", "upgrade", "head"]`
  (image WORKDIR is apps/web-server, which holds alembic.ini).
- Env only: `DATABASE_URL` from `secretKeyRef` name `.Values.postgres.externalSecretName`,
  key `database-url` (same as deployment.yaml), and `PYTHONDONTWRITEBYTECODE=1`.
  No ConfigMap (does not exist at pre-install).
- Pod labels `pfactory.selectorLabels` + `app.kubernetes.io/component: migrations`
  (Service requires `component: server`, so not an endpoint). No `serviceAccountName`; `automountServiceAccountToken: false`.
  `podSecurityContext` / `containerSecurityContext` from values, nodeSelector and
  tolerations as the Deployment, `/tmp` emptyDir mount, `restartPolicy: Never`,
  `backoffLimit: .Values.migrations.jobBackoffLimit`.
- autoApply=true renders nothing new.

## Steps

1. charts/pfactory/templates/job-migrations.yaml (new): the Job above, styled
   ported from AIFactory PR #2004.
   → verify by `helm lint charts/pfactory && helm template t charts/pfactory | grep -A3 'hook": pre-install,pre-upgrade'`
   Traps: the Service selector must keep excluding the pod (component != server); the chart SA
   does not exist at pre-install.
2. charts/pfactory/values.yaml `migrations:` block (~line 197) and
   charts/pfactory/README.md `migrations` row (~line 39): rewrite the comment
   to say the pre-install/pre-upgrade hook Job runs `alembic upgrade head`
   before pods roll (no init container), and that the DB Secret must exist
   before the first install (ExternalSecret-/bundled-managed Secrets do not
   exist yet at pre-install: pre-create it or install once with autoApply=true).
   → verify by `helm lint charts/pfactory`
   Traps: comment-only, keep keys and defaults unchanged.
3. tests/helm/test_migrations_job.py (new), same shape as
   tests/helm/test_audit_anchor_toggle.py (`@pytest.mark.helm`, `_render`):
   default render has exactly one Job with component `migrations`, hook
   annotation `pre-install,pre-upgrade`, command ending `upgrade head`,
   DATABASE_URL secretKeyRef `pfactory-db`/`database-url`,
   automountServiceAccountToken false; `migrations.autoApply=true` renders no
   such Job; `migrations.jobImage=x/y:z` sets the image.
   → verify by `.venv/bin/python -m pytest tests/helm/test_migrations_job.py -q -m helm`
   Traps: the test must fail on origin/dev (no Job).

## Tests

- `helm lint charts/pfactory` passes.
- `.venv/bin/python -m pytest tests/helm -q` passes.

## Rollback

Revert the PR; the template and test are additive and the values change is comment-only.
