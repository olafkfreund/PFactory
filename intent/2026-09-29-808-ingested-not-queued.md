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

## Correction: the remap I first recommended is wrong

I proposed remapping `ingested` → `review`, then read the canonical taxonomy
(`Factory/apis/status-taxonomy.json`, the hub source of truth this file cites).
It does not support that:

- **`queued`** — "Queued / not-started markers — **no agent attached yet**. NOT
  terminal." Tokens: `backlog`, `pending`, `queued`, `todo`, `icebox`, `draft`.
- **`review`** — "Parked-for-review markers — **finished executing**, awaiting a
  human/AI decision."

An ingested session has not finished executing; it has not started. So `review`
would be a false statement about it, and `queued` — "not started, no agent
attached yet" — is the *correct* classification. `service_helpers.py:98`
independently maps `ingested` → `backlog`, which is itself a `queued` token, so
the two mappings already agree.

**So the mapping is right and the metric is wrong.** KEDA is using a state that
means "not started, and may never start" as though it meant "work waiting for a
worker". PFactory has no queue consumer for it (measured above), so no mapping
change in this repo can make that query meaningful — it can only make it
differently inaccurate. The two production rows are a true statement: those
sessions are queued and nothing will ever pick them up.

That moves the fix out of this repo. Options, none of which I should pick alone:

- **A — change the KEDA metric** (factory-gitops#273): scale on something a
  replica can act on (in-flight `running` rows, or HTTP concurrency). PFactory
  changes nothing. This is the only option that makes the autoscaler correct.
- **B — end abandoned rows** (the issue's second suggestion, and #360's shape):
  give ingested-and-never-processed sessions a terminal path — a TTL sweep, or
  the plan-session DELETE that #798 is already asking for. Narrows the phantom
  depth without fixing what the metric means.
- **C — both**: A for correctness now, B because rows that live forever are their
  own problem regardless of KEDA.

My recommendation is **C, with A first** — but A is a factory-gitops change, so
this issue as filed ("do not count ingested sessions as admission-queued") cannot
be satisfied here as written. Worth deciding before any code is written.

## Open questions

1. **Which option — A, B or C?** See above. My recommendation is C with A first.
2. **If B: TTL sweep or wait for #798's DELETE?** #798 wants a plan-session
   DELETE for 34 discarded sessions; that endpoint would give the abandon path
   somewhere to land, so doing B first may duplicate it.
3. **The two production rows** still need a one-off `job_states` write, which is
   a production DB change and needs your explicit go-ahead whatever we choose.
