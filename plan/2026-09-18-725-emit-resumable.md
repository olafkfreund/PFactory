---
status: approved
issue: 725
spec: spec/2026-09-18-725-emit-resumable.md
---

# Plan: Live emit blocks the server and loses track of what it created

Approved decisions (from the spec):

- `emit_to_github(..., on_progress=None)`: called with
  `(epic_number, child_numbers_so_far)` after the epic exists and after each
  child is created; reused children included; callback errors caught + logged,
  never abort creation; never called on dry run.
- `PlanService.emit` passes a callback that sets `emitted_issue_number`, sets
  `emit_result = EmitResult(dry_run=False, epic_number, child_numbers).model_dump()`
  and calls `_save`. Final write after return unchanged. No new field.
- Routes `emit` and `emit_contract` run the service call via
  `asyncio.to_thread`; request/response unchanged; no admission cap.
- `PlanService._emit_locks: dict[str, threading.Lock]` (created under
  `_store_lock`); `emit` and `emit_contract` non-blocking acquire per session,
  held → `PlanServiceError("an emit is already running for session '<id>'")`
  (route 400); release in `finally`; dry runs included. In-process is enough
  (`replicaCount: 1`).
- `testing_strategy.py:266` `handover:tfactory` → `handoff:tfactory`.
- No title-dedup against the repo; no probe change.

## Steps

1. `apps/backend/plan/emit/github_emitter.py`: add `on_progress` parameter and a
   small `_report(...)` helper that calls it inside try/except (log warning).
   Call after the epic is created/reused and after each child is added to
   `child_numbers` (reused ones seeded first). → verify by the emitter test in
   step 6 (call sequence).
2. `apps/backend/plan/service.py`:
   a. `__init__`: `self._emit_locks: dict[str, threading.Lock] = {}`.
   b. `_emit_lock(session_id)`: context manager — get/create the lock under
      `_store_lock`, `acquire(blocking=False)` or raise the "already running"
      `PlanServiceError`, release in `finally`.
   c. `emit`: wrap the body in `with self._emit_lock(session_id):`; pass
      `on_progress=` a closure doing the three writes above (live only).
   d. `emit_contract`: wrap its body in the same lock.
   → verify by steps 6-7 tests.
3. `apps/web-server/server/routes/plan_pipeline.py`: `import asyncio`;
   `emit` → `await asyncio.to_thread(SERVICE.emit, session_id, repo=..., ...)`;
   `emit_contract` → same for `SERVICE.emit_contract`. → verify by step 8.
4. `apps/backend/plan/synthesize/testing_strategy.py:266`: fix the label.
   `tests/test_synthesize.py:87`: assert `handoff:tfactory` present and
   `handover:tfactory` absent. → verify `pytest tests/test_synthesize.py`.
5. `git grep -n "handover:tfactory" -- apps tests` → only the docstring at
   `testing_strategy.py:7`, which is corrected in the same edit.
6. `tests/test_emit_resumable.py` (new), emitter level:
   - `on_progress` receives the epic first, then a growing child map, one call
     per created child; reused children appear in the first call;
   - a callback that raises does not stop creation (all issues created, a
     warning logged);
   - dry run never calls it.
7. `tests/test_emit_resumable.py`, service level (approved session built as in
   `tests/test_plan_service.py`, `PlanService(store_dir=tmp_path, persist=True)`):
   - **kill + resume**: fake gh (from `tests/test_emit.py` pattern) that raises
     `class _Killed(BaseException)` on the 4th child create. `emit` raises
     `_Killed`. New `PlanService(store_dir=tmp_path, persist=True)`: session has
     `emit_result.dry_run is False`, the epic number, 3 child numbers,
     `status == "approved"`. Re-emit with a fresh fake gh: no epic created,
     exactly `len(children) - 3` children created, status `emitted`.
   - **concurrent emit**: fake gh whose `create_issue` blocks on a
     `threading.Event`; thread A emits; main thread's emit raises
     "already running"; release; A completes; epic created once.
   - **lock released after failure**: after the `_Killed` case, a second emit
     on the same service is not refused.
8. `tests/test_emit_resumable.py`, route level: monkeypatch `pp.SERVICE.emit`
   (and `.emit_contract`) with a stub that `time.sleep(0.5)`s and returns a
   session; `asyncio.gather(pp.emit(...), ticker())` where `ticker` counts
   `asyncio.sleep(0.01)` iterations until the emit finishes → ticks > 20.
9. Negative controls (not committed):
   - drop `on_progress=` in `PlanService.emit` → kill+resume test fails
     (no numbers after restart / duplicates);
   - call `SERVICE.emit` directly in the route → tick test fails;
   - make the lock a no-op → concurrent test fails.

## Tests

    apps/backend/.venv/bin/pytest tests/test_emit_resumable.py tests/test_emit.py tests/test_gh_runner.py \
      tests/test_plan_service.py tests/test_plan_completion.py tests/test_hard_routing.py \
      tests/test_readiness_e2e.py tests/test_synthesize.py tests/test_plan_persistence.py -q

Expected: all pass. Full backend suite runs in the pre-commit hook.

## Rollback

Revert the commit. Sessions saved mid-emit by the new code carry an
`emit_result` in the existing schema (`dry_run: false` + numbers), which the
old code already reads as a prior partial emit (#119), so no data cleanup.
