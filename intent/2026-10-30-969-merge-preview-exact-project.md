---
status: draft
issue: 969
author: olafkfreund
---

# Intent: worktree merge preview by exact project id

## Problem

`GET /api/tasks/{task_id}/worktree/merge-preview` (`get_worktree_merge_preview`
in `apps/web-server/server/routes/tasks.py`) takes a bare spec id and scans every registered project for a
matching `.pfactory/specs/<id>` directory. Its project key is the whole bare id, so it
never names the project it reads: in shadow mode (`RBAC_ENFORCE_TASK_ROUTES` off)
a caller can read another org's merge preview by spec id alone.
#958 fixed the sibling `status` and `diff` routes the same way; this one was
left out because its callers had not been checked. Refs olafkfreund/Factory#312.

## Proposed outcome

Merge preview needs `project_id:spec_id`. A bare id gets 400. An unknown
project id finds nothing, even when another project has that spec. The exact id
works as before.

## Affected users and systems

- `apps/web-server/server/routes/tasks.py` (one handler).
- Callers, checked: the web UI (`apps/frontend-web/src/lib/api-adapter.ts`
  `mergeWorktreePreview(task.id)`, called from `useTaskDetail.ts`) already sends
  `task.id`, which is `project_id:spec_id`. Today that id is refused by the
  handler's single-component check, so the UI preview is currently broken; this
  fixes it. The CLI `--merge-preview` is local git, not this endpoint. No MCP
  proxy, CFactory or AIFactory code calls it (AIFactory calls its own copy).

## Constraints

- Reuse `_exact_project_path` from #958. No spec scan.
- Keep the spec component path-safe (split_task_id).
- No schema, config or deploy change.

## Open questions

None. Pre-approved by the user for the 2026-10-30 wave.
