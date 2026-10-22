---
status: approved
issue: 808
---

# Intent: an ingested session that nobody processes stays `queued` forever

## Problem

`lifecycle_state_for()` maps PFactory's `ingested` to the canonical `queued`
(`apps/web-server/server/jobstore/lifecycle.py`). Ingest mirrors the row at
once (`PlanService._store` → `_save` → `_mirror`), so a plan is `queued` from
the moment it is ingested. A session that is then never processed and never
discarded stays there for good.

Production has two such rows from 2026-09-04 (`002-loopback-probe`,
`003-loopback2-1788521204`). The KEDA ScaledObject counts `queued` rows as
queue depth, so they read as permanent work.

## Measured, not assumed

- **Nothing in PFactory consumes `queued`.** No worker, poller or claim path
  selects rows by `lifecycle_state = 'queued'`. `try_start` counts `running`
  rows only; `in_flight_count()` has no callers. The only reader is KEDA.
- **The mapping is right.** The hub taxonomy defines `queued` as "not started,
  no agent attached yet", which is what an ingested session is.
  `service_helpers.py` maps `ingested` → `backlog`, also a `queued` token.
- **Explicit exits already end the row.** `discard()` (#360) writes
  `discarded`, which maps to `failed` (terminal). `DELETE` (#798/#821) only
  accepts `discarded` or `rejected` sessions, so a deleted session's row is
  already terminal before the delete. Both production rows are `ingested`:
  nobody discarded them.

So the gap is a session that is ingested and then left alone. No code path
ever moves it out of `queued`.

## Decision (owner, 2026-10-22): option B, in PFactory

Abandoned ingested sessions get a terminal path in PFactory. Recorded options:

- **A — change the KEDA metric:** rejected for this repo. It belongs to
  factory-gitops#273, which lifts the one-replica pin, and is done there.
- **B — end abandoned rows in PFactory:** chosen.
- **C — both:** A is tracked separately, so this change is B only.

## Desired outcome

An ingested session that nobody processes or discards leaves `queued` on its
own after a bounded time, through the same terminal path a human discard
uses, so it stops counting as queue depth and stops sitting on the board.

## Affected

- `apps/backend/plan/service.py` (session lifecycle).
- The web server's background loops and settings
  (`apps/web-server/server/main.py`, `config.py`, `background/`).
- Tests for the new terminal path.

## Constraints

- Do not touch the KEDA metric (factory-gitops#273).
- No production write from this change. The two existing rows are ended by the
  new path once it runs, or by the owner's one-off cleanup, which waits for the
  cluster to come back.
- The ingest path must stay best-effort: no new failure mode there.
- A session someone is still about to process must not be ended too early.

## Open questions

1. How long is "abandoned"? (Proposed in the spec: 7 days, configurable.)
