---
status: draft
issue: 865
author: olafkfreund
---

# Intent: invariants run in production — readiness, timer, metric

Implements [RFC-0024](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0024-package-owned-runtime-invariants.md)
§4 (Factory#818). The RFC's decisions are approved; this intent carries them
over and does not reopen them.

## Problem

Registered invariants never run in production. `factory_invariants`
(`apps/backend/factory_invariants/__init__.py:170-177`) is off unless
`FACTORY_INVARIANTS=1`, and nothing in the web server imports the companions
or calls `check_all()`. A check that never runs looks exactly like one that
passes.

## Proposed outcome

- `register(..., critical=False)`: the owning package opts each check in.
- `GET /api/ready` runs the critical checks via `check_all()`, caches the
  result for one probe period (10 s), and returns 503 naming the failing
  packages; otherwise 200.
- Non-critical checks run on an in-app timer (default 5 min). Each violation
  logs at error level naming the package and increments
  `factory_invariant_violations_total{service,package}`.
- `FACTORY_INVARIANTS` defaults on; `=0` turns all checks off. The allowlist
  and blocklist (`FACTORY_INVARIANTS_ONLY`, `FACTORY_INVARIANTS_SKIP`) stay.

## Affected users and systems

PFactory web server (`apps/web-server/server`), the registry, and anyone
scraping `/metrics`. No probe moves here: the readiness probe moves to
`/api/ready` in factory-gitops#283, after this is deployed.

## Constraints

- `/api/health` must not change: it is also the liveness probe, so failing it
  would restart-loop the pod.
- Critical checks make no network calls. A new check runs non-critical for one
  release before it may be marked critical, so this change marks none critical.
- `/api/ready` must be reachable without auth (kubelet probes carry no token).
- Factory-gitops is out of scope.

## Open questions

None. Endpoint, cache period, timer default, metric name and switch semantics
are fixed by RFC-0024 §4.
