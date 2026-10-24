---
status: draft
issue: factory-gitops#303
author: olafkfreund
---

# Intent: PFactory pods drain running runs before they stop

## Problem

When a PFactory pod is told to stop (KEDA scale-down, rollout, a manual pod
delete), the event loop cancels the agent process monitor, and its
`CancelledError` handler marks every running task failed
(`apps/web-server/server/services/agent_process_monitor.py:1054-1062`,
`_fail_interrupted_run` at :296). The lifespan shutdown
(`apps/web-server/server/main.py:194-215`) does not wait, and the gitops
manifest has no `terminationGracePeriodSeconds` (default 30s) and no preStop
hook. So every scale-down kills in-flight coding runs. factory-gitops#303 wants
to raise the PFactory KEDA ceiling, which makes scale-downs more frequent.

## Proposed outcome

- A pod that is asked to stop while it holds a run lease keeps serving until
  those runs finish, up to a bounded grace period, then exits.
- A run longer than the grace period still ends as an honest `failed`, never
  stuck `active`.
- A pod with no runs stops about as fast as today.

## Affected users and systems

- PFactory web server (`apps/web-server`): one new public endpoint.
- factory-gitops `apps/pfactory/manifests/manifests.yaml`: pod grace period and
  a preStop hook on the `pfactory` container.
- Operators: a rollout or scale-down can now take up to 30 minutes while a long
  run is in flight.

## Constraints

- The gitops change must not merge before a PFactory release that serves
  `/api/drain` is live; otherwise curl always fails and every shutdown waits
  about 29 minutes.
- The endpoint is unauthenticated (the kubelet exec hook has no token) and
  exposes only a count, like `/api/ready`.
- No DB query: the count comes from the in-process `run_leases._WATCHERS`.
- `/api/ready` does not change: failing readiness would pull the pod from the
  Service, which is not the goal.

## Open questions

None. Plan jobs in `job_states` (no owner column) are out of scope; they are
reclaimed after `PFACTORY_PLAN_LEASE_TTL_SECONDS=600`.
