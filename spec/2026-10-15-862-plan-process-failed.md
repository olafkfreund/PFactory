---
status: approved
issue: 862
intent: intent/2026-10-15-862-plan-process-failed.md
---

# Spec: save a failed status when process() raises

## Design

In `PlanService.process()` (`apps/backend/plan/service.py:1183-1214`), wrap
the pipeline from `session.status = "processing"` through the final
`self._save(session)` in `try/except Exception`:

```python
except Exception as exc:
    logger.exception("process() failed for session %s", session_id)
    session.status = "failed"
    session.process_error = type(exc).__name__
    with contextlib.suppress(StaleSessionError):
        self._save(session)
    raise
```

- New field on `PlanSession` (`service.py:94`):
  `process_error: str | None = None` — the exception class name only, so no
  exception text reaches API callers (`server/error_ref.py` rule). The full
  trace goes to the log; the job row keeps the message as today
  (`service.py:1375-1379`). A later successful `process()` clears it to None.
- `failed` is already a known native status in the job store lifecycle map
  (`web-server/server/jobstore/lifecycle.py`, `"failed": "failed"`), so the
  durable row and admission slot handling are unchanged.
- Board column: add `"failed": "human_review"` to `_BOARD_COLUMN`
  (`plan/service_helpers.py:87-101`), like `rejected`: it needs a person.
  Without it the default is `backlog`, which hides the failure.
- Portal: add `'failed'` to `PlanSessionStatus`
  (`frontend-web/src/shared/types/plan.ts:275`), a destructive badge in
  `STATUS_BADGE` and to `canProcess` (`SessionDetail.tsx:25-33, 73`) so the
  user can re-run it. `process()` has no status guard, so re-running works.
- `StaleSessionError` from the failure save is suppressed: it means another
  replica changed the session, which must not be overwritten (#758); the
  original exception still propagates.

## Alternatives rejected

- Read the job_states row in the status endpoints: two stores for one status
  is the bug class this audit found (Factory#971); the session is the reader's
  source.
- Store `str(exc)` on the session: can carry tokens or paths to API callers.
- Save `processing` / `reviewing` as they happen: more writes and still no
  terminal status on failure.

## Risks

- A reader or filter that does not know `failed` (any `status ==` list):
  checked with `git grep` during implementation; the lifecycle map and board
  map are the shared ones.
- A failed session in `human_review` could be approved? `approve()` requires
  a review; a failed run never sets one on this path. Checked in the plan.

## Verification

`tests/test_plan_process_failed.py`: make `_decompose` raise in a
`PlanService`; `process()` re-raises; `service.get(id).status == "failed"`,
`process_error == "ValueError"`, board state `human_review`. With a session
store configured (persist on), a fresh service reading the same store sees
`failed`. Fails on `origin/dev`, passes after. A portal vitest for the
badge is not added (type + map entry only).
