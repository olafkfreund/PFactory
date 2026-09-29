---
status: draft
issue: 808
---

# Intent: an ingested session counts as KEDA queue depth forever

## Problem

`lifecycle_state_for()` maps PFactory's `ingested` to the canonical `queued`
(`apps/web-server/server/jobstore/lifecycle.py:36`). Ingest mirrors the row
immediately (`PlanService._store` → `_save` → `_mirror`), so a plan is `queued`
from the moment it is ingested. A session that is then never processed and never
discarded stays there permanently.

The KEDA ScaledObject scales on `count(*) … WHERE lifecycle_state = 'queued'`, so
those rows read as standing queue depth. Production has two, both from
2026-09-04 (`002-loopback-probe`, `003-loopback2-1788521204`), and the HPA shows
2/2 today. At `maxReplicaCount: 1` that is invisible; above 1 it brings a
scale-out forward with no work behind it.

## Measured, not assumed

- **Nothing consumes `queued`.** Grepped the whole of `apps/web-server` and
  `apps/backend`: no worker loop, poller or claim path selects rows by
  `lifecycle_state = 'queued'`. The only reader is KEDA.
- **The admission cap does not count it either.** `try_start` counts rows already
  `running` (`jobstore/store.py:520-539`) — not `queued`. `in_flight_count()`,
  which does use `IN_FLIGHT_LIFECYCLE = ("queued", "running")`, has **no callers
  anywhere in the repo**. So the mapping's only live consumer is the autoscaler.
- **Processing is caller-driven.** `process()` is invoked from the API, not from
  a queue sweep — `ingested` is "waiting for someone to ask", not "waiting for a
  free worker".

That is the actual defect: `queued` in this taxonomy means work a pod can pick
up, and PFactory has no such queue. The metric is measuring the wrong thing, not
merely counting stale rows.

## Desired outcome

The KEDA queue-depth metric reflects work a replica can actually take on, so the
one-replica pin (factory-gitops#273) can be lifted without the autoscaler acting
on phantom depth. An ingested-and-abandoned session must not hold queue depth
indefinitely.

## Affected

- `apps/web-server/server/jobstore/lifecycle.py` — the native→canonical map.
- Whatever the change implies for the KEDA ScaledObject query (in
  `factory-gitops`, a separate repo — coordination, not an edit here).
- Tests covering the mapping, plus the scale-relevant assertion.
- Two existing production rows need a one-off `job_states` write. **Out of scope
  for this change and needs the owner's explicit go-ahead** — the code fix must
  stand on its own.

## Constraints

- `lifecycle_state` is the Factory hub's canonical taxonomy
  (`apis/status-taxonomy.json`), shared with CFactory and any sibling replica.
  Remapping a status changes what those consumers see, so the choice has to be
  defensible against the taxonomy, not just convenient for KEDA.
- #360's lesson is in a comment right there in the file: a status missing from
  the map fell through to the `running` fallback and leaked 43 admission slots
  over six days. Any change here needs the same explicitness.
- Best-effort mirroring must stay best-effort; no new failure mode on the ingest
  path.

## Open questions

1. **Which canonical state should `ingested` map to?** My reading: `review` —
   the taxonomy's "awaiting human action" bucket, which is exactly what an
   ingested-but-unprocessed plan is, and where `processed`/`approved` already
   sit. The alternative, keeping `queued` and having the discard/abandon path end
   the row (the issue's second suggestion), leaves the metric structurally wrong:
   a plan ingested and simply left alone still reads as depth until someone
   discards it.
2. **Does any consumer outside this repo rely on `ingested` → `queued`?** Worth
   one grep across the hub/CFactory before committing to the remap; I have only
   verified this repo.
3. **Does the KEDA query need changing too, or does the remap suffice?** If
   `queued` becomes genuinely empty at all times, scaling on it is dead — the
   pin lift may need a different metric (`running` depth, or HTTP concurrency).
   That is a factory-gitops decision; this issue may only be able to make the
   metric *honest*, not useful, and that should be stated rather than glossed.
