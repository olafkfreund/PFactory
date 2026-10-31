---
status: draft
issue: 975
spec: spec/2026-10-31-975-worktree-resolve-exact-project.md
coders:   # the user asked for agy as a team member this wave
  1: agy
  2: agy
---

# Plan: worktree resolve writes by exact project id; viewer check on worktree reads

Approved decisions (user: exactly the issue's Fix, mirror #969):
- `resolve-uncommitted` and `resolve-git-merge` need `project_id:spec_id`; a bare
  id gives HTTP 400; the project comes from `_exact_project_path`; no scan.
- `GET /{task_id}/worktree/merge-preview`, `/status`, `/diff` get
  `dependencies=[Depends(require_task_access("viewer"))]`, shadow mode (no flag change).
Callers: the web UI sends `task.id` (`project_id:spec_id`,
`apps/frontend-web/src/lib/api-adapter.ts:411-414`); nothing else calls these.

## Steps

1. `apps/web-server/server/routes/tasks.py`:
   a. `resolve_uncommitted_conflicts` (~2821) and `resolve_git_merge_conflicts`
      (~3077): replace the "#335 sanitize" try/except and the whole "Find the
      task across all projects" block (`project_path = None` ... for/else) with
      exactly what `get_worktree_merge_preview` (~2250-2282) does: 400 if
      `":" not in task_id` (same detail text), `project_id, spec_id = split_task_id(task_id)`,
      read projects.json (keep "No projects configured"), `project_path = _exact_project_path(...)`,
      `if not project_path: return {"success": False, "error": f"Task {task_id} not found"}`,
      spec dir `.pfactory/specs/<spec_id>` must exist (same error), worktree path
      `.pfactory/worktrees/tasks/<spec_id>`. Keep the existing "No worktree found" check
      and everything after it unchanged (the stash message may keep `task_id`).
   b. Add `dependencies=[Depends(require_task_access("viewer"))]` to the three
      `@router.get` decorators: `/{task_id}/worktree/merge-preview` (~2249),
      `/{task_id}/worktree/status` (~3919), `/{task_id}/worktree/diff` (~4027).
   → verify by `ruff check` and `ruff format --check` on the file.
   Traps: `_exact_project_path` is defined later in the module (fine at call time);
   check `project_path` for None separately so mypy narrows it (the #969 ratchet trap).
2. Tests:
   a. `tests/test_rbac_console_and_mcp.py`: add `test_resolve_writes_need_exact_project`,
      parametrized over `resolve_uncommitted_conflicts` and `resolve_git_merge_conflicts`,
      mirroring `test_merge_preview_needs_exact_project`: bare `s1` → HTTPException 400;
      `nope:s1` → `{"success": False, "error": "Task nope:s1 not found"}`;
      `p1:s1` with no worktree dir → `{"success": False, "error": "No worktree found for this task"}`.
   b. `tests/test_task_route_rbac.py`: add to `TASK_ROUTES["tasks"]["viewer"]` the three
      GET paths; in `test_every_listed_route_carries_the_dependency` treat them as
      `("GET",)` like `agent-console/sse`; bump `n` 49 → 52.
   c. Same file: give `make` an optional `path=None, role="member"` (used for the task
      kind when given) and add `test_worktree_routes_log_would_deny_in_shadow`,
      parametrized over the five worktree paths with their role (`/tasks` prefix):
      flag off, user `member-a` with `X-Key-Org: B` → 200, `caplog` has
      `rbac.would_deny`, one audit with `details["shadow"] is True`.
   → verify by running both test files (lead runs them) and confirming 2a fails on origin/dev.

## Tests

`cd apps/backend && PYTHONPATH=. .venv/bin/pytest ../../tests/test_rbac_console_and_mcp.py ../../tests/test_task_route_rbac.py -q`;
mypy ratchet `scripts/ratchet_lint.py` if present; CI.

## Rollback

Revert the PR. No schema or config change.
