---
status: approved
issue: 798
spec: spec/2026-09-29-798-plan-session-delete.md
---

# Plan: DELETE a terminal plan session, and stop listing the discarded

Carried over from the approved intent and spec:

- Deletable statuses are **`discarded` and `rejected` only**. Do **not** reuse
  `plan/completion.py`'s `TERMINAL_STATUSES` — it includes `emitted`, whose
  record is the audit trail for real GitHub epics. This is the central trap.
- The store delete happens **before** popping `_sessions`, so a store failure
  cannot leave a session the store still has.
- A wrong-tenant id returns the **same 404 as an unknown id** — the endpoint
  must not be usable to probe other tenants' ids.
- `actor` travels in a `DeleteBody(_StrictBody)`, matching `/discard` and
  `/reject` and inheriting `extra="forbid"` (#671).
- The delete is audited via `log_audit_event`; it swallows its own failures in a
  SAVEPOINT, so audit trouble must never fail the delete.
- Endpoint and list default ship together (intent decision 3).

## Steps

1. **`apps/web-server/server/jobstore/plan_session_store.py`** — add
   `delete(self, session_id: str) -> bool` running
   `DELETE FROM plan_sessions WHERE session_id = :sid` through the existing
   `_run(coro)` seam, returning `rowcount > 0`. Follow the shape of `get` /
   `_get_coro`.
2. **`apps/backend/plan/service.py`** —
   a. add `def delete(self, session_id: str) -> bool: ...` to the `SessionStore`
      Protocol (~:360);
   b. add module-level `DELETABLE_STATUSES = frozenset({"discarded", "rejected"})`
      with a comment saying why it is not `TERMINAL_STATUSES`;
   c. add `PlanService.delete_session(session_id, *, actor, tenant_id=None) -> dict`
      per the spec: unknown → `PlanInputError`; wrong tenant → the *same*
      `PlanInputError`; status not deletable → `PlanInputError` naming the
      status; store delete, then `_sessions.pop` under `_store_lock`; return
      `{"session_id", "status", "title"}`;
   d. `list_sessions(*, tenant_id=None, include_discarded=False)` filters
      `status == "discarded"` unless asked.
3. **`tests/fake_session_store.py`** — add `delete()` to `FakeSessionStore`
   (the Protocol is structural; every fake must satisfy it).
4. **`apps/web-server/server/services/audit_service.py`** — add
   `ACTION_PLAN_SESSION_DELETE = "plan_session.delete"` beside the other
   `ACTION_*` constants.
5. **`apps/web-server/server/routes/plan_pipeline.py`** —
   a. `DeleteBody(_StrictBody)` with `actor: str`, `reason: str | None = None`;
   b. `@router.delete("/{session_id}")` taking `db: AsyncSession = Depends(get_db)`
      (both already imported), mapping `PlanInputError` → 404 for unknown/tenant
      and 409 for a non-deletable status, and calling `log_audit_event` after a
      successful delete with `details={"actor", "reason", "status", "title"}`;
   c. `list_sessions` (:173) gains `include_discarded: bool = False` and passes
      it through.
6. **Other `list_sessions` consumers** — `apps/backend/plan/agent_api.py:89` and
   the MCP path (`mcp_stdio/router.py:387` → the route) inherit the new default.
   That is intended (they are the same portal-facing surface); check neither
   passes positional args that would break.

## Tests

New `tests/test_plan_session_delete.py`:

1. discard, then delete → returns the summary, and the session is absent from
   `list_sessions()`;
2. delete an `ingested` session → refused;
3. **delete an `emitted` session → refused**, asserting on the status in the
   message. This is the test that would catch a reviewer "simplifying" step 2b
   into `TERMINAL_STATUSES`;
4. delete a `rejected` session → allowed;
5. unknown id → refused;
6. with multi-tenancy on, another tenant's id → refused with the *same* error as
   an unknown id (assert the messages match, not merely that both raise);
7. `list_sessions()` hides a discarded session; `include_discarded=True` shows it;
8. route-level: `DELETE` returns 200, a second `DELETE` returns 404, and a
   non-deletable status returns 409.

`tests/postgres/test_plan_session_store.py`: `delete` removes the row and
returns `True`; deleting an absent id returns `False`.

Commands:

    apps/backend/.venv/bin/pytest tests/test_plan_session_delete.py -q
    apps/backend/.venv/bin/pytest tests/ -q -k "plan_session or plan_discard or plan_service"
    apps/backend/.venv/bin/pytest tests/ -q -m "not slow and not integration"

Negative controls (run, then revert — neither is committed):

- widen `DELETABLE_STATUSES` to `TERMINAL_STATUSES` ⇒ test 3 fails;
- drop the `include_discarded` filter ⇒ test 7 fails.

## Deviation: the cq-ratchet counts net-new per rule per file

Found by CI on PR #821, which failed seven jobs. The first implementation used a
relative parent import for `audit_service` (two net-new TID252, one per imported
name), `db: AsyncSession = Depends(get_db)` (net-new B008), a bare `-> dict` and
an unannotated `result` (net-new `type-arg` and `no-any-return`), and read
`result.rowcount` directly (net-new `attr-defined`, because SQLAlchemy's
`Result` has no declared `rowcount`).

Each of those matches a pattern the touched files already contain, which is why
it looked acceptable — but the ratchet compares counts per rule per file, so
matching precedent still fails. Corrected to: an absolute
`from server.services.audit_service import ...`, `Annotated[AsyncSession,
Depends(get_db)]`, `-> dict[str, str]` with an annotated `result`, and the
existing `_rowcount` helper in `jobstore/store.py` (which exists for exactly
this reason). All four counters now equal `origin/dev`'s, measured in a worktree
at `origin/dev` rather than against the branch.

Lesson for future steps: run
`scripts/ratchet_lint.py --base origin/dev --package ...` — or, where its mypy
half cannot run locally, compare per-file counts against a worktree at
`origin/dev` — before pushing. Comparing against the branch's own committed
state measures nothing.

## Rollback

Revert the commit. The endpoint disappears, the list shows discarded sessions
again, and `plan_sessions` rows are untouched — nothing migrates, so there is no
schema to undo. Rows already deleted are gone; that is the feature.

## Out of scope

Teaching the hub probe (`Factory/scripts/parr_regression.py:218`) to call the new
endpoint — different repo. File a follow-up there once this ships; until then the
probe keeps leaving one row per night, and this change stops the portal showing
them.

## Delegation note

Implementation is delegated to a Sonnet subagent against this plan; the steps
above are written to be followed literally. Verification (the negative controls
and the suite) is re-run here before anything is committed.
