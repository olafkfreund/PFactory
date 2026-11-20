---
status: approved
issue: 1099
author: olafkfreund
---

# Intent: Project-scoped authorization, filtered lists, and indirect-path checks

## Problem

PFactory currently runs project RBAC in shadow mode (`RBAC_ENFORCE_TASK_ROUTES = False`), logs denials on partial coverage instead of blocking, returns 403 instead of 404 when a project is invisible to a principal (leaking project existence), ignores token scope ceilings on REST/WebSocket routes, and leaves several project-reaching surfaces unguarded:
- List endpoints (`GET /api/projects`, `GET /api/tasks`, `GET /api/tasks/running`, `GET /api/pfactory/tasks`) return items across all organizations.
- Sub-routers under `/api/projects/{projectId}` (`changelog.py`, `context.py`, `github.py`, `git.py`, `insights.py`, `mcp.py`, `auto_fix.py`) and read routes in `projects.py`, `tasks.py`, and `execution.py` lack project role checks.
- Indirect paths reach project data without project-id path parameters: absolute filesystem paths (`/api/files/*`, `/api/git/*`, `/api/github/detect-repo`, `/api/github/remote`, worktree IDE/terminal openers), bare `spec_id` lookups (`/api/pfactory/tasks/{spec_id}/*`), and `repo`/`project_id` body fields (`/api/plan/sessions/*`, `/api/github/*`).

## Proposed outcome

1. Every direct and indirect route, WebSocket, PTY/rmux bridge, and MCP tool that accesses project data enforces `require_project_role(principal, project_id, min_role)` with role levels `viewer < member < admin < owner`.
2. Unknown or invisible projects (no `>= viewer` grant) raise `404`; visible projects where effective role `< min_role` raise `403`.
3. Effective role is `min(team_grant_role, token_scope_ceiling)` for scoped tokens (`acw_`) and `team_grant_role` for user sessions (OIDC/JWT).
4. List endpoints return only projects (and tasks, plan sessions, terminals, and pfactory specs) where the principal has at least `viewer`.
5. The legacy shared token (`APP_API_TOKEN`) keeps full access only while `LEGACY_SHARED_TOKEN_FULL_ACCESS` (`APP_LEGACY_SHARED_TOKEN_FULL_ACCESS`, default `True` in phase 2) is enabled, logging a deprecation warning on each use. Single-user/local mode (`DISABLE_AUTH=True`) resolves a default team with the local user as `owner`.
6. A generated route-table test walks REST routes, WebSockets, and MCP tools and fails if any project-scoped entry lacks the authorization check.

## Affected users and systems

- `apps/web-server/server/` (`config.py`, `auth.py`, `database/engine.py`, `routes/project_authz.py`, project/task/file/git/github/plan/pfactory/terminal routes, WebSockets, and `mcp_stdio` / `mcp_remote`).
- `parr-run`, cluster jobs, and local single-user sessions using `APP_API_TOKEN` or `DISABLE_AUTH`.

## Constraints

- Must match the shared contract across `AIFactory#2181`, `PFactory#1099`, and `TFactory#1650` (`Factory#3857` phase 2).
- Must not break running cluster automation or `parr-run` (`LEGACY_SHARED_TOKEN_FULL_ACCESS` defaults to `True` in phase 2).
- Must not modify vendored `apps/web-server/factory_common/` or `apps/backend/runners/github/`, and must pass the ruff lint ratchet.
- Must release the request DB transaction (`await db.rollback()`) before spawning background audit tasks (`#1936`).

## Open questions

1. **Legacy `acw_` keys with only `"api"` scope:** For existing `acw_` keys minted before granular scopes (`project:write`, `task:write`, `plan:write`, `task:merge`) were attached alongside `"api"`, should `"api"` alone cap at `member` (with `mcp:write` -> `member` and `task:merge` required for `admin`/merge) or `viewer`?
2. **Filesystem discovery before project registration:** Should `POST /api/projects/scan` and `GET /api/files/discover` allow any authenticated user who belongs to at least one org (`creator_org(user, db) is not None`) while filtering out directories already registered to foreign orgs?

## Decisions (coordinator, 2026-11-20, under the owner's standing delegation)

1. An `acw_` key with only the legacy `api` scope caps at `member`, matching AIFactory#2181.
2. `POST /api/projects/scan` and `GET /api/files/discover` are allowed for any authenticated user who belongs to an org. Directories already registered to a foreign org are filtered out.
