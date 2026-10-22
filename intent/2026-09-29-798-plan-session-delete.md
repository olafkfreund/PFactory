---
status: approved
issue: 798
---

# Intent: a plan session can be abandoned but never removed

## Problem

There is no way to remove a plan session. `POST /{id}/discard` marks it
`discarded` (#360) and that is the end of the road: the row stays in
`plan_sessions` forever, and `GET /api/plan/sessions` returns every session
regardless of status, so a terminal `discarded` session sits in the portal list
looking like live work.

The hub's nightly PARR probe says so in its own source
(`Factory/scripts/parr_regression.py:218`):

> PFactory has no plan-session DELETE, so its probe session is discarded

So the probe does the most correct thing available to it and the residue is
structural. This morning the list held 40 sessions, **34** of them
`parr-regression probe`, every one `discarded`, created daily at ~03:17 UTC
since 2026-09-08. Earlier probes left the same kind of debris under other names
(`e2e-758 emit lease probe`, `e2e-779 plan review probe (safe to close)`). The
rows were cleared by hand (backed up, each verified `status=discarded`, matched
by explicit id list, 40 → 3) — a workaround that tomorrow's run undoes.

## Outcome

1. An automated caller can clean up after itself: a `DELETE` that removes a
   session it has already abandoned, and refuses anything else.
2. The portal list stops showing abandoned work by default, with an explicit
   way to ask for it.

Both, per the issue: a caller that can clean up, and a list that does not
present the dead as live.

## Affected

- `apps/web-server/server/routes/plan_pipeline.py` — no DELETE route exists;
  `list_sessions` (:173) passes only a tenant filter.
- `apps/backend/plan/service.py` — `PlanService` has no `delete_session`; the
  `SessionStore` Protocol (:368) has `get` / `list_payloads` / `upsert` and no
  `delete`.
- `apps/web-server/server/jobstore/plan_session_store.py` — no `delete`.
- The portal's session list (consumer of the changed default).
- **Out of scope, different repo:** teaching the hub probe to call the new
  endpoint (`Factory/scripts/parr_regression.py`). Worth a follow-up issue
  once this ships, since the endpoint is the thing it has been waiting for.

## Constraints

- **`emitted` must not be deletable.** `plan/completion.py` already defines
  `TERMINAL_STATUSES = {emitted, rejected, discarded}`, but an emitted session
  produced real GitHub epics and issues; its record is the audit trail for
  them. Deletion is for `discarded` and `rejected` only, which is also what the
  issue asks for. Reusing `TERMINAL_STATUSES` unchanged here would be wrong.
- Multi-tenancy (#308): a caller must not delete another tenant's session.
- Deletion is irreversible and crosses replicas, so it must go through the
  shared store, not just the process-local `_sessions` dict.
- Changing the list default is a visible behaviour change for any existing
  consumer that counts on seeing discarded rows.

## Decisions (were open questions, answered at approval)

1. **A delete is audited.** "Who removed this and when" is the one question a
   missing session provokes, and the hash-chained log (#806) exists for exactly
   this class of operation. The record outliving the session is the point.
2. **`DELETE` requires an `actor`,** as `/discard` and `/reject` do — it is what
   makes the audit record something other than anonymous.
3. **Endpoint and list default ship together.** One visible behaviour change for
   portal consumers instead of two.
