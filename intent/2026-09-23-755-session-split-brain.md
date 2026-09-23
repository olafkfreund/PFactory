---
status: approved
issue: 755
author: Olaf Krasicki-Freund
---

# Intent: Plan sessions are cached per process, so replicas disagree

## Problem

`PlanService` loads every session into `self._sessions` once at startup
(`_load_all`, `apps/backend/plan/service.py:391`), `get()` reads only that dict
(:580), `list_sessions()` only that dict (:569), and `_save()` writes to disk
without telling any other process. Verified on `dev` today; all three claims in
the issue hold.

Measured in prod (issue #755, image `sha-816c981`, 4 replicas): five sessions
discarded via the API returned 200 and the file on disk read `discarded`, while
three of four pods kept serving `ingested` until a rollout restart. Reads
through the Service therefore depend on which pod answers.

Two things found while scoping change the shape of the fix:

1. **The mitigation has already landed.** factory-gitops `c298538`
   (2026-09-23) pins the KEDA `ScaledObject` to `maxReplicaCount: 1`, citing
   this issue. Prod is single-replica again, so this is no longer bleeding —
   what remains is making >1 possible.
2. **The PVC is `ReadWriteOnce` on `local-path`**
   (`factory-gitops apps/pfactory/manifests/manifests.yaml:5-6`), not a shared
   network volume. Replicas only ever saw the same files because they were
   co-scheduled on one node. A pod scheduled elsewhere would get a *different*
   local directory and diverge silently — worse than a stale cache, because
   nothing would even look inconsistent on a single pod.

So the issue's option 2 (read-through / mtime on the shared file) is not a
multi-replica story on this storage: it makes replicas agree only while they
share a node by accident of scheduling.

## Proposed outcome

- PFactory can serve plan sessions correctly from more than one replica, so the
  KEDA pin can be lifted deliberately rather than left as a permanent guard.
- A write on any replica is visible to every replica immediately.
- Session ids are allocated without collision across processes
  (`self._seq = len(self._sessions)`, :378, is per process today).
- No silent divergence path remains: either replicas share one authoritative
  store, or running >1 is refused loudly.

## Affected users and systems

- Everything that reads a session through the Service: the cockpit, the MCP
  server, CFactory's poll, `/pfactory-watch`.
- `apps/backend/plan/service.py` (the store), the durable `job_states` path
  already wired there (`_job_store` / `_mirror`, :387/:441), and the
  factory-gitops KEDA pin once lifted.

## Constraints

- Single-replica behaviour and the on-disk JSON layout must keep working: local
  dev, the CLI and the tests all use it with no database.
- The durable store is optional (`DATABASE_URL` unset ⇒ in-memory + JSON).
  Whatever lands must degrade to today's behaviour, not require Postgres.
- No silent data loss during migration: sessions already on the PVC must remain
  readable.

## Open questions

1. Which direction? (a) Move sessions into Postgres alongside `job_states`
   (the direction the gitops comment and RFC-0016 already state), with the JSON
   store kept as the no-database fallback; or (b) keep the pin at 1
   indefinitely and treat multi-replica as out of scope, documenting it.
   Recommendation: (a) — `job_states` carries only lifecycle + terminal
   payload, so it needs a `plan_sessions` table (or a payload column), but it
   is the only option that makes the pin liftable.
2. Should the service *refuse to start* (or log an ERROR) when it detects >1
   replica without a durable store configured, so this cannot recur silently?
   Recommendation: yes, a loud startup check — the existing WARNING was present
   during the incident and nobody saw it.
