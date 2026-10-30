---
status: draft
issue: 969
spec: spec/2026-10-30-969-merge-preview-exact-project.md
coders:   # the user asked for Codex as a team member this wave
  1: codex
  2: codex
---

# Plan: worktree merge preview by exact project id

Approved decisions: merge preview requires `project_id:spec_id` (bare → 400),
finds the project by exact id with the existing `_exact_project_path`, and never
scans other projects. Callers checked: the web UI already sends `task.id`
(`project_id:spec_id`); nothing else calls this endpoint.

## Steps

1. `apps/web-server/server/routes/tasks.py`, `get_worktree_merge_preview`: replace the bare-id sanitize and the
   "Find the task across all projects" loop with the status-route pattern: 400
   on a bare id, split + sanitize (split_task_id), `_exact_project_path`, then
   `.pfactory/specs/<spec_id>` must exist else `{"success": False, "error": f"Task {task_id} not found"}`.
   Use `spec_id` for the spec dir and the worktree path. Keep the `test_plan.json`
   read and the rest of the handler as is.
   → verify by `ruff check` and `ruff format --check` on the file.
   Traps: `_exact_project_path` is defined later in the module (fine at call time).
2. `tests/test_rbac_console_and_mcp.py`: add `test_merge_preview_needs_exact_project`: bare `s1` → HTTPException
   400; `nope:s1` → `{"success": False, "error": "Task nope:s1 not found"}`;
   `p1:s1` with no worktree dir → `{"success": False, "error": "No worktree found for this task"}`
   (proves the exact project was found). Mirror the existing exact-project test.
   → verify by running that test (lead runs it: Codex's sandbox hangs on aiosqlite)
   and confirming it fails on origin/dev.

## Tests

`cd apps/backend && PYTHONPATH=. .venv/bin/pytest ../../tests -q` with the
Postgres `DATABASE_URL` CI uses; ratchet as CI runs it.

## Rollback

Revert the PR. No schema or config change.
