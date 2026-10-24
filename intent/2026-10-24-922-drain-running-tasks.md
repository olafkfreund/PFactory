---
status: draft
issue: 922
author: olafkfreund
---

# Intent: the drain sees every running task, and the Helm chart drains too

## Problem

Two gaps were left after #920 (`GET /api/drain`) and factory-gitops#306 (the
preStop drain):

1. `/api/drain` counts only live run-lease watchers
   (`run_leases._WATCHERS`, `apps/web-server/server/main.py:547-553`). A
   coding task in `agent_service.running_tasks` that holds no lease yet does
   not show up. If a scale-down lands in that window, the preStop hook sees
   200, the pod stops, and the task is marked failed.
2. The Helm chart (`charts/pfactory`) has no preStop drain, and its grace
   period is 45s. Chart installs keep the old behaviour: SIGTERM fails every
   running task.

## Proposed outcome

- `/api/drain` returns 503 while the pod has any unfinished agent process,
  with or without a lease.
- A default chart install gets the same drain as the gitops manifest:
  `terminationGracePeriodSeconds: 1800` and a preStop loop that polls
  `/api/drain` for up to 1740s. Both can be turned off or tuned in values.

## Affected users and systems

- `apps/web-server` (`/api/drain`), on every PFactory pod.
- `charts/pfactory` users (Helm installs). The k3d `factory` cluster uses the
  gitops manifest, not the chart, so its behaviour only changes through the
  endpoint.

## Constraints

- The response shape `{"held": n}` and the 200/503 contract stay as they are.
  factory-gitops#306 relies on them.
- `/api/drain` stays public and must not create the agent service if the pod
  has none yet.
- The chart's preStop command matches factory-gitops#306 exactly, including
  `--max-time 5`.
- Hooks stay on (no `--no-verify`).

## Open questions

None.
