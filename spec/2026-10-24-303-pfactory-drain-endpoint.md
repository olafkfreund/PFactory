---
status: draft
issue: factory-gitops#303
intent: intent/2026-10-24-303-pfactory-drain-endpoint.md
---

# Spec: PFactory pods drain running runs before they stop

## Design

Two changes, shipped in this order.

**1. PFactory: `GET /api/drain`** (`apps/web-server/server/main.py`, next to
`/api/ready` at :536).

- Count the live leases this process holds:
  `held = sum(not t.done() for t in run_leases._WATCHERS.values())`.
  `_WATCHERS` (`apps/web-server/server/services/run_leases.py:45`) holds one
  watcher task per (kind, key) for task, reply, changelog and review runs. Each
  watcher removes itself when its run ends (:293). The image runs one process
  (`Dockerfile:377`, `python -m server.main`), so this count is exact for the pod.
- `held == 0` → 200 `{"held": 0}`; otherwise 503 `{"held": n}`.
- Add `"/api/drain"` to `TokenAuthMiddleware.PUBLIC_PATHS`
  (`apps/web-server/server/auth.py:220-229`). The hook calls it without a token.
  It exposes only a count, like `/api/ready`.

**2. factory-gitops: grace period and preStop** (`apps/pfactory/manifests/manifests.yaml`).

- Pod spec, next to the pod `securityContext` (:58):
  `terminationGracePeriodSeconds: 1800`.
- `pfactory` container, next to the probes (:430):

  ```yaml
  lifecycle: { preStop: { exec: { command: ["sh","-c","end=$(( $(date +%s) + 1740 )); while [ $(date +%s) -lt $end ]; do curl -fs http://localhost:3114/api/drain >/dev/null && exit 0; sleep 10; done; exit 0"] } } }
  ```

  The loop gives up 60s before the grace period ends. The process then still
  gets SIGTERM and `_fail_interrupted_run` records an honest failure, instead of
  a SIGKILL leaving the task `active` (the #863 problem).
- Opened as a DRAFT PR, titled "... (merge only after the PFactory release with
  /api/drain is live)", `Refs #303`.

## Alternatives rejected

- **Longer KEDA `stabilizationWindowSeconds`** (`apps/keda/scaledobjects/pfactory.yaml:24`):
  KEDA scales on queued plan jobs, not running coding tasks. It delays the kill
  but never protects the run.
- **Waiting in the lifespan shutdown**: SIGTERM already cancels the monitor
  tasks before shutdown runs; waiting there changes ordering in shared code and
  is still capped by the 30s default grace.
- **Folding into `/api/ready`**: a 503 there drops the pod from the Service and
  stops new work only as a side effect; a busy pod is not unready.
- **DB count of leases by `OWNER`**: same answer with a DB round trip and a
  failure mode when the DB is down during shutdown.

## Risks

- Gitops merged before the image has the endpoint: curl gets 404 (or 401) for
  29 minutes on every shutdown. Mitigated by the draft PR and title.
- Rollouts and scale-downs can take up to 30 minutes while a long run is live.
- `sh`, `date` and `curl` must exist in the main container. curl is installed
  (`Dockerfile:190`); the chainguard `-dev` base ships busybox. Checked in the
  live test, not assumed.
- Plan jobs (`job_states`, no owner column) are not counted; they are reclaimed
  after 600s as today.

## Verification

- Unit test: a not-done fake task in `run_leases._WATCHERS` → 503 `{"held":1}`;
  a done task or empty dict → 200 `{"held":0}`; no token needed.
- Unit test: `"/api/drain" in TokenAuthMiddleware.PUBLIC_PATHS`.
- Gitops: `kubectl kustomize`/dry-run renders; after the PFactory release is
  live, start a long task, delete its pod, and see it stay Terminating until
  the task completes, then exit with no "could not persist interrupted run".
  Repeat with a task longer than the grace: it ends `failed`, not `active`.
