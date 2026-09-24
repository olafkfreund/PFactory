---
status: approved
issue: 758
author: Olaf Krasicki-Freund
---

# Intent: one emit per plan session, across every replica

Follow-up to #755 (`intent/2026-09-23-755-session-split-brain.md`).

## Problem

Emitting a plan (`emit()`, `emit_contract()`) creates the epic and child
issues on the target repo. Two emits of the same session must never overlap:
#725 fixed exactly that after a double-click produced 27 duplicate issues.

The guard is `PlanService._emit_locks`, a per-process dict of
`threading.Lock` (`apps/backend/plan/service.py:491`, taken in `_emit_lock`
around line 1598). It only holds within one process. #757 made plan sessions
shared across replicas through the Postgres session store, but it left this
lock per-process. With more than one PFactory replica, two pods can emit the
same session at once. Both read `emitted_issue_number = None`, and both
create an epic.

Today that cannot happen only because factory-gitops#268 pins the KEDA
ScaledObject to one replica. The #725 spec justified the in-process lock with
"replicaCount is pinned to 1" in the chart, but KEDA was overriding the chart,
so the justification was already false when it was written.

## Proposed outcome

- At most one emit (issues or contract) runs per plan session at a time,
  however many PFactory replicas are running.
- A second, concurrent emit of the same session waits or is refused. It never
  creates a second epic.
- With no shared store (`DATABASE_URL` unset), behaviour is unchanged: the
  in-process lock still applies.
- After this ships, raising `maxReplicaCount` above 1 no longer risks
  duplicate emission (the pin itself is lifted separately).

## Amendment (approved 2026-09-23): no lost session writes

Review of release PR #760 found that `PlanSessionStore.upsert` overwrites the
shared row unconditionally. A long `process()` or emit holding a payload read
earlier can overwrite a newer approval or discard saved by another replica,
which is the lost update #755 set out to prevent. It shares this task's cause
(no store-level concurrency guard) and was folded in with the user's
approval.

Added outcome: a session write based on a stale read never overwrites a newer
one. The stale writer is refused and told why; the newer state survives.

## Affected users and systems

- PFactory backend: `plan/service.py` emit paths and the session store
  (`SessionStore` and its Postgres implementation from #757).
- Anyone who emits a plan, whether from the portal, the MCP `plan_*` tools or
  CFactory's approve-plan path.
- Target repositories, which currently risk duplicate epics and issues if
  replicas are unpinned.
- factory-gitops: the KEDA pin can be revisited after this lands (not in this
  task).

## Constraints

- No duplicate issues on a target repo under any interleaving of two replicas.
- The lock is never held across anything unbounded without a timeout. The
  emit makes many GitHub calls, so a crashed holder must not block the
  session forever.
- Must work on the existing Postgres store; no new infrastructure.
- Tests must prove it across processes, not threads.
- No change to emit behaviour when `DATABASE_URL` is unset.

## Open questions

Resolved 2026-09-23 (approved): 1 = fail fast with 409; 2 = settle in spec, advisory lock preferred; 3 = confirm in spec.

1. **What a concurrent second emit gets:** block and wait for the first to
   finish, then see `emitted_issue_number` set and return the existing result,
   or fail fast with "emit already in progress"? **Recommended:** fail fast
   with a clear 409. The UI already guards double-clicks, and a waiting
   request can outlive the HTTP timeout.
2. **Lock mechanism:** a Postgres session-level advisory lock keyed by session
   id (released automatically if the connection dies), or
   `SELECT ... FOR UPDATE` on the session row, as `_durable_admit` does? To be
   settled in the spec. **Leaning towards** the advisory lock, because the
   emit is long and a row lock would pin a transaction open for its whole
   duration.
3. **Id allocation** (`_store_lock` / `next_seq`): #757 already claims atomic
   allocation in the store. Confirm in the spec that no per-process lock is
   still load-bearing there; if one is, include it here.
