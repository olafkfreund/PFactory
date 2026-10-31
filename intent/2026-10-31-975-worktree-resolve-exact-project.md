---
status: draft
issue: 975
author: olafkfreund
---

# Intent: worktree resolve writes by exact project id; viewer check on worktree reads

## Problem

1. `POST /api/tasks/{task_id}/worktree/resolve-uncommitted` and
   `/worktree/resolve-git-merge` (`apps/web-server/server/routes/tasks.py`)
   take a bare spec id and scan every registered project for it. Their
   `require_task_access("member")` check keys on the whole bare id, which is
   no project, so with `APP_RBAC_ENFORCE_TASK_ROUTES` off a caller from another
   org can write to another org's project through them. The web UI sends
   `project_id:spec_id`, which the current `safe_spec_component(task_id)`
   rejects (`:` is not allowed), so the UI buttons get "invalid task id" today.
2. `GET .../worktree/merge-preview`, `/status` and `/diff` already need the
   exact project id (#958, #969) but carry no role check at all.

## Proposed outcome

- Both resolve writes need `project_id:spec_id`; a bare id gets 400; the
  project is found by exact id, never by scanning.
- The three worktree GETs carry `require_task_access("viewer")`, shadow mode
  like every other task route: with the flag off a different-org caller is
  logged as `rbac.would_deny` and audited, not blocked.

## Affected users and systems

PFactory web-server task routes; the web UI's conflict-resolve buttons (they
start working again). TFactory has the same fix in #1479 (separate lane).

## Constraints

- Mirror #969 (merge preview) exactly; no new helpers.
- Shadow mode only: no enforcement flag change, no lockout.

## Open questions

None. Decision given: exactly the issue's Fix.
