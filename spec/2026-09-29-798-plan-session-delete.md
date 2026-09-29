---
status: approved
issue: 798
intent: intent/2026-09-29-798-plan-session-delete.md
---

# Spec: DELETE a terminal plan session, and stop listing the discarded

## Design

Four layers, smallest change at each.

### 1. Store — `PlanSessionStore.delete`

    def delete(self, session_id: str) -> bool:
        """Remove the row; True when one was removed."""

A plain `DELETE FROM plan_sessions WHERE session_id = :sid`, run through the
existing `_run(coro)` background-loop seam like every other method. No CAS: the
caller has already established the session is terminal, and a concurrent write
to a session being deleted is a contradiction the 409 path cannot make better.

`SessionStore` (the Protocol in `plan/service.py:360`) gains the same signature.
It is structural, so the test fake must grow the method too.

### 2. Service — `PlanService.delete_session`

    DELETABLE_STATUSES = frozenset({"discarded", "rejected"})

    def delete_session(self, session_id: str, *, actor: str,
                       tenant_id: str | None = None) -> dict:

- unknown id → `PlanInputError` (404 at the route, as elsewhere);
- status not in `DELETABLE_STATUSES` → `PlanInputError` naming the status;
- `tenant_id` set and mismatched → the same not-found error as an unknown id,
  so the endpoint cannot be used to probe other tenants' ids;
- deletes from the store first, then pops `_sessions` under `_store_lock`;
- returns a small dict (`session_id`, `status`, `title`) — enough for the
  response and the audit details, since the session is gone afterwards.

**`DELETABLE_STATUSES` is deliberately not `completion.TERMINAL_STATUSES`.**
That set includes `emitted`, and an emitted session is the record of real GitHub
epics and issues. Reusing it would let a caller destroy an audit trail.

### 3. Route — `DELETE /api/plan/sessions/{session_id}`

    class DeleteBody(_StrictBody):
        actor: str
        reason: str | None = None

Mirrors `DiscardBody` (#360) and inherits `extra="forbid"` (#671). Takes
`db: AsyncSession = Depends(get_db)` — the router already imports both — and
after a successful delete calls

    await log_audit_event(db, action=ACTION_PLAN_SESSION_DELETE,
                          resource_type="plan_session", resource_id=session_id,
                          details={"actor": ..., "reason": ..., "status": ...,
                                   "title": ...})

with a new `ACTION_PLAN_SESSION_DELETE = "plan_session.delete"` beside the other
`ACTION_*` constants. `log_audit_event` swallows its own failures inside a
SAVEPOINT, so an audit problem cannot fail the delete — and the record outliving
the session is the point (intent decision 1).

Status codes: 200 with the summary; 404 unknown/other tenant; 409 wrong status.

### 4. List — hide discarded by default

`PlanService.list_sessions(*, tenant_id=None, include_discarded=False)` filters
`status == "discarded"` unless asked; the route takes `?include_discarded=true`.
Only `discarded` is hidden — `rejected` is a plan someone may still fix, and the
issue asks only for the discarded.

## Alternatives rejected

- **`actor` as a query parameter** instead of a body. More robust (some proxies
  and clients drop a DELETE body), but every sibling mutation here takes
  `actor` in a `_StrictBody`, and a query param loses the 422-naming-the-field
  behaviour. Consistency wins; noted as the one thing to revisit if a real
  caller loses its body.
- **Soft delete (`deleted_at`).** The issue is rows accumulating forever; a
  soft delete accumulates them under a different name.
- **Only filtering the list** (option 2 in the issue). Hides rather than
  removes; tomorrow's probe still adds a row.
- **Reusing `TERMINAL_STATUSES`.** See above — it would permit deleting
  `emitted`.
- **Deleting from `_sessions` before the store.** A store failure would then
  leave the process without a session the store still has, and the next list
  would resurrect it.

## Risks

- **Irreversible, by design.** Mitigated by the status gate, the required
  `actor`, and the audit record.
- **The list default is a visible change** for any consumer counting discarded
  rows. The `?include_discarded=true` escape covers it; the portal is the only
  known consumer.
- **A delete racing another replica's write** to the same session: the write
  lands on a missing row, which `_upsert_session` already reports as
  `StaleSessionError` ("was deleted on another replica"). No new path.
- **Cross-tenant probing** via 404-vs-409 distinction: both unknown-id and
  wrong-tenant return the same 404, so the endpoint reveals nothing.

## Verification

1. `tests/test_plan_discard.py` (or a new `test_plan_session_delete.py`):
   delete after discard succeeds and the session is gone from `list_sessions`;
   delete of an `ingested` session is refused; delete of an `emitted` session is
   refused *by name*, since that is the trap in reusing `TERMINAL_STATUSES`;
   unknown id is refused; another tenant's id is refused with the same error as
   unknown.
2. `tests/postgres/test_plan_session_store.py`: `delete` removes the row and
   returns True; deleting an absent id returns False.
3. List default: a discarded session is absent by default and present with
   `include_discarded=True`.
4. Negative control: make `delete_session` accept any status ⇒ the
   `emitted`/`ingested` cases fail.
5. Full suite, since the Protocol gains a method every fake must satisfy.
