---
status: approved
issue: 975
intent: intent/2026-10-31-975-worktree-resolve-exact-project.md
---

# Spec: worktree resolve writes by exact project id; viewer check on worktree reads

## Design (chosen: the issue's Fix, user decision)

In `apps/web-server/server/routes/tasks.py`:

- `resolve_uncommitted_conflicts` and `resolve_git_merge_conflicts`: replace
  the `safe_spec_component(task_id)` sanitize and the "Find the task across all
  projects" loop with the pattern `get_worktree_merge_preview` uses since #969:
  `":" not in task_id` gives `HTTPException(400)`; `split_task_id` (validates the
  spec half, 400 on traversal); `_exact_project_path(projects_data, project_id)`;
  `.pfactory/specs/<spec_id>` must exist, else
  `{"success": False, "error": f"Task {task_id} not found"}`. Paths use `spec_id`.
  The member dependency then keys on the real project id.
- `get_worktree_merge_preview`, `get_worktree_status`, `get_worktree_diff`: add
  `dependencies=[Depends(require_task_access("viewer"))]`, as the
  `logs/watch` and `agent-console/sse` routes do. `TaskAccessChecker` is shadow
  unless `RBAC_ENFORCE_TASK_ROUTES` is on.

## Alternatives rejected

- Keep accepting a bare id and resolve the project from it: that is the scan
  this issue removes; a bare id cannot name one project.
- Enforce the viewer check now: other task routes are still shadow; the flag
  flip is a separate decision.
- A shared "resolve task project" helper for all five routes: more churn than
  the two call sites justify; #969 inlined the same few lines.

## Risks

- A caller that still sends a bare id to the resolve routes gets 400. The UI
  sends `task.id` (`project_id:spec_id`), which those routes reject today, so
  nothing working breaks.
- The viewer dependency adds one DB lookup per worktree GET (same as other
  task routes); in shadow mode a lookup error allows.

## Verification

- Unit: bare id raises 400 on both writes; `nope:s1` gives "not found";
  `p1:s1` with no worktree gives "No worktree found for this task".
- Route guard table: the three GETs carry `("TaskAccessChecker", "viewer")`;
  the two writes keep `member`.
- Shadow: on each of the five paths, a caller whose API key is bound to
  another org passes (flag off) and logs `rbac.would_deny` with a shadow audit.
- CI green, mypy ratchet unchanged.
