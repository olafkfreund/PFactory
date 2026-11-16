---
status: approved
issue: review-fixes (Factory#1016, #1015, #1012, #1004)
spec: spec/2026-11-16-review-fixes-pfactory.md
coders:
  1: codex   # small tricky logic (env/tenant/endpoint rules); lead reviews each diff
reviewers:
  - codex    # independent read-only review of the final diff
---

# Plan: PFactory cross-PR review fixes

Carries every decision of the approved spec (header-authoritative tenant with 403 on mismatch; RUNNER_STRIP filter in the PTY only; agent-type to cap map threaded through tool registry and coder save path; endpoint keys stay allowlisted but ignored when a host credential exists).

## Steps

1. `apps/web-server/server/pty/session.py:~58`: replace `os.environ.copy()` with a filter on `RUNNER_STRIP`; import from `..utils.subprocess_env` → `pytest tests/test_terminal_ownership.py`
   Traps: keep ANTHROPIC_API_KEY (user shell).
2. `apps/web-server/server/routes/contract_events.py:_tenant`: header authoritative, 403 on mismatch, body only when no header → `pytest tests/test_contract_events_feedback.py`
   Traps: the existing body-tenant test sends no header.
3. `apps/backend/memory/retention.py`, `agents/tools_pkg/registry.py`, `agents/tools_pkg/tools/memory.py`, `core/client.py:816`, `agents/memory_manager.py`, `agents/session.py`: wire agent type and "build" to the caps → `pytest tests/test_memory_retention.py`
4. `apps/web-server/server/utils/project_env.py`: `_ENDPOINT_CREDENTIALS`, host-key snapshot, ignore paired endpoint → `pytest apps/web-server/tests/test_project_env_allowlist.py`
   Traps: keep the settings-route allowlist test passing.

## Tests

`pytest tests/ apps/web-server/tests/ -m "not slow" -q` green; the four new tests fail on origin/dev (verified by reverting source only).

## Rollback

Revert the PR; no data or schema change.
