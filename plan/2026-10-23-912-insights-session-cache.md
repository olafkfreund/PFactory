---
status: draft
issue: 912
spec: spec/2026-10-23-912-insights-session-cache.md
---

# Plan: insights chat keeps every message across pods

Decision: drop the per-process `_sessions` cache, so every load reads the session file. The file format is unchanged.

## Steps

1. `apps/web-server/server/services/insights_service.py`: remove the following lines.
   - `self._sessions` (`:107`);
   - the cache read (`:158-159`) and the cache writes (`:154`, `:191`);
   - the delete eviction (`:276-277`).

   Check: `grep -n "_sessions\b" apps/web-server/server/services/insights_service.py` finds nothing.
   Traps: keep `_running_tasks`; it is the #805 registry.
2. Add `apps/web-server/tests/test_insights_session_cache.py`. Two services share one project path. A saves M1, then B loads and saves M2, then A loads and saves M3. All three messages end up in the file. The test must fail before step 1.

   Check: `pytest tests/test_insights_session_cache.py` from `apps/web-server`.
   Traps: none.

## Tests

- `pytest tests/test_insights_session_cache.py tests/test_insights_error_leak.py`

## Rollback

Revert the PR.
