---
status: approved
issue: 862
spec: spec/2026-10-15-862-plan-process-failed.md
---

# Plan: save a failed status when process() raises

Approved decisions (from the spec):

- `process()` catches `Exception` around its pipeline, logs it, sets
  `status = "failed"` and `process_error = type(exc).__name__`, saves
  (suppressing `StaleSessionError`), and re-raises the original.
- New `PlanSession.process_error: str | None = None`; a successful run sets it
  back to None.
- Board: `"failed": "human_review"`. Lifecycle map already knows `failed`.
- Portal: `'failed'` in `PlanSessionStatus`, destructive badge, allowed in
  `canProcess`.
- Not changed: `approve()` (`service.py:1709`) still only needs a review, so a
  failed re-process of a previously reviewed session can be approved, as it
  could while stuck at `processing` before. Recorded, not fixed here.

## Steps

1. `apps/backend/plan/service.py`:
   - `:94` `PlanSession`: add `process_error: str | None = None` with a one-line
     comment (#862), near `discard`.
   - `:22`: `from contextlib import contextmanager` → also import `suppress`.
   - `:1183-1214` `process()`: keep `external_runner` defaulting and
     `session = self.get(session_id)` outside the try (an unknown id stays a
     plain `PlanServiceError` with nothing to save). Put the rest up to and
     including `self._save(session)` inside `try:`; set
     `session.process_error = None` just before `session.status = "processed"`.
     `except Exception as exc:` → `logger.exception(...)`, status/error,
     `with suppress(StaleSessionError): self._save(session)`, `raise`.
     `emit_usage_snapshot` and `return` stay after the try.
   - `:71` comment listing statuses: add `failed`.
   → verify by `ruff check apps/backend/plan/service.py` (no new findings vs origin/dev).
   Traps: `logger` name — use the module's existing logger variable; `_save`
   raises only `StaleSessionError` (`:697`).
2. `apps/backend/plan/service_helpers.py:87-101`: add `"failed": "human_review"`
   with a one-line why.
   → verify by `git grep -n '"failed"' apps/backend/plan/service_helpers.py`.
3. `apps/frontend-web/src/shared/types/plan.ts:275-282`: add `| 'failed'`.
   `apps/frontend-web/src/components/planning/SessionDetail.tsx:25-33`: add
   `failed: { variant: 'destructive', label: 'failed' }`; `:73` canProcess
   also true for `'failed'`.
   → verify by `npx tsc --noEmit -p apps/frontend-web` (or the repo's
   typecheck script) if node_modules exist; otherwise note it was CI-checked.
   Traps: `Record<PlanSessionStatus, …>` makes the badge entry mandatory once
   the type has `failed`; grep other `Record<PlanSessionStatus` maps and add
   `failed` to each.
4. `tests/test_plan_process_failed.py`: `PlanService(store_dir=tmp_path,
   persist=True)`, `ingest_text` a small plan, monkeypatch
   `svc._decompose` to raise `ValueError("boom")`; assert `process()`
   raises `ValueError`, `svc.get(id).status == "failed"`,
   `process_error == "ValueError"`, `board_state() == "human_review"`, and
   `PlanService(store_dir=tmp_path, persist=True).get(id).status == "failed"`
   (restart reads it from disk). Second test: after a failure, un-patch and
   re-run `process()` → `processed`, `process_error is None`.
   → verify fails on origin/dev (detached worktree at
   `<scratchpad>/pf-base`), passes after.

## Tests

    /mnt/code/Source-home/GitHub/PFactory/apps/backend/.venv/bin/pytest tests/test_plan_process_failed.py tests/test_plan_concurrency.py tests/test_emit_resumable.py tests/test_plan_stale_write.py -q
    git grep -n "Record<PlanSessionStatus" apps/frontend-web/src

Expected: all pass.

## Rollback

Revert the commits. Sessions saved as `failed` then read as an unknown status:
board `backlog`, lifecycle `failed`; re-processing still works.
