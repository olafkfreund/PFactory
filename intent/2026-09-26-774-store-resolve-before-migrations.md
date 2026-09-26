---
status: approved
issue: 774
author: Olaf Krasicki-Freund
---

# Intent: The first boot after a migration runs per-process, silently

## Problem

`PlanService` resolves the shared session store when it is constructed. The
module-level `SERVICE` is lazy (PEP 562), but
`apps/web-server/server/routes/plan_pipeline.py:31` does
`from plan.service import SERVICE` at **module import**, and `main.py` imports
its routers before the lifespan handler runs `init_db()` /
`MIGRATIONS_AUTO_APPLY`. So on the first boot after a migration that creates
`plan_sessions`, the table does not exist yet when the store is resolved.

Measured in prod on the 0.6.20 deploy (`sha-afc667c`, #774):

- `13:30:17.097` — `plan.service` warns the table is not ready, plan sessions
  stay per-process;
- `13:30:17.601` — migrations run and create the table, half a second later.

`_resolve_session_store` then records the URL in `_SESSION_STORE_UNAVAILABLE`
and never retries, so that pod served its whole life per-process:
`plan_sessions` stayed empty, the 27 on-disk sessions were never imported, and
the emit lease + compare-and-set writes built on the store were inactive. A
manual `rollout restart` fixed it.

That negative cache is mine: I added it in #755 so an unusable store was not
rebuilt (leaking a loop, thread and pool) per `PlanService`. It stopped the
leak and made the failure permanent.

The KEDA pin at one replica is what keeps this from being a split brain today;
the visible symptom is milder and worse in a way — the new safety features are
off and nothing says so after that first warning.

## Proposed outcome

- A pod that boots against an unmigrated database picks the store up once the
  migration lands, without a restart.
- No regression of the leak: an unavailable store is still closed, and
  resolution is not attempted afresh on every call.
- The "we are running per-process" state is visible for as long as it lasts,
  not only in one line at boot.

## Affected users and systems

- `apps/backend/plan/service.py` (`_resolve_session_store`,
  `_SESSION_STORE_UNAVAILABLE`, `PlanService`), and whatever the fix touches in
  `apps/web-server/server/main.py` startup ordering.
- Every deploy that adds a migration; today that means the safety work from
  #755 / #765 / #766 is inactive on the first pod after such a deploy.

## Constraints

- Must not reintroduce the per-construction leak (#755 review).
- Must keep working with no `DATABASE_URL` at all (local dev, CLI, tests) and
  when the DB is genuinely unreachable — degrade, never raise on a read.
- No import-order trap that a future route import can silently re-create.

## Open questions

1. Fix by ordering (run migrations before the routes import `SERVICE`) or by
   making the store resolution retryable (drop the permanent negative cache for
   a cooldown, re-attempt on use, import on-disk sessions when it first
   succeeds)? Recommendation: the retry. Ordering fixes this deploy but leaves
   a rule ("never import SERVICE before startup") that nothing enforces, and
   the same latent failure returns the next time an import moves.
