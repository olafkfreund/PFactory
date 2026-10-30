---
status: draft
issue: 969
intent: intent/2026-10-30-969-merge-preview-exact-project.md
---

# Spec: worktree merge preview by exact project id

## Design

In `get_worktree_merge_preview` (`apps/web-server/server/routes/tasks.py`), do what `get_worktree_status` does
since #958: if `":"` is not in `task_id`, raise `HTTPException(400, "Task ID must
include project ID (format: project_id:spec_id)")`; split it (split_task_id), load
`projects.json`, and take the project with `_exact_project_path(projects_data,
project_id)`. No project, or no `.pfactory/specs/<spec_id>` in it, returns the existing
`{"success": False, "error": "Task {task_id} not found"}`. Everything after
the lookup uses `spec_id` where it used the bare `task_id` (spec dir, worktree
path). The rest of the handler is unchanged.

## Alternatives rejected

- Keep the bare id and filter the scan by the caller's org: duplicates the RBAC
  layer inside one handler, and the sibling routes already chose exact ids.
- Accept both forms: a bare id keeps the cross-org scan.

## Risks

- A caller sending a bare id gets 400. Checked: the only HTTP caller (web UI)
  sends `project_id:spec_id`.

## Verification

Handler-level test in `tests/test_rbac_console_and_mcp.py`: bare id → 400; `nope:s1` does not find p1's
spec; `p1:s1` finds it (reaches the worktree lookup). It must fail on dev.
Full suite on Postgres and the lint ratchet as CI runs them.
