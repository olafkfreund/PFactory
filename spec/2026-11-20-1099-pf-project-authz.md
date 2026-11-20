---
status: draft
issue: 1099
intent: intent/2026-11-20-1099-pf-project-authz.md
---

# Spec: Project-scoped authorization (`require_project_role`), filtered lists, and indirect-path checks

## Design

### 1. Shared authorization primitive (`apps/web-server/server/routes/project_authz.py`)

Define `ROLE_LEVELS = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}` and make `require_project_role(principal, project_id, min_role="viewer", *, db)` the single enforcement bottleneck:
- **Project & grant resolution:** Look up `project_id` in `load_projects()`. If missing, raise `HTTPException(404, "Project not found")`. Resolve `project_org_id = project.get("org_id")` (in single-user `DISABLE_AUTH` mode, unstamped projects default to `"default"`). If `project_org_id` is missing, raise `404`.
- **Single-user / legacy service token:**
  - When `settings.DISABLE_AUTH` is `True`, the local principal (`id="default"`) belongs to the seeded `"default"` org as `owner`.
  - When a request authenticates via the legacy shared token (`APP_API_TOKEN`), `auth.py` and `mcp_stdio/auth.py` check `settings.LEGACY_SHARED_TOKEN_FULL_ACCESS` (`APP_LEGACY_SHARED_TOKEN_FULL_ACCESS`, default `True`). When `True`, log a deprecation warning (`logger.warning("DEPRECATED: legacy shared token used...")`) and grant `is_service=True` (`owner` access). When `False`, reject the legacy shared token with `401/403`. Scoped `acw_` keys never set `is_service=True`.
- **Effective role (`grants ∩ scopes`):**
  - Query `OrgMember` for `(org_id=project_org_id, user_id=principal["id"])` to obtain `grant_role`. For org-bound `acw_` keys (`principal.get("org_id")`), if `principal["org_id"] != project_org_id`, `grant_role` is `None`.
  - If `grant_role is None`, release the transaction (`await db.rollback()`), record the audit event, and raise `HTTPException(404, "Project not found")` (identical 404 for unknown and invisible projects).
  - Compute `ceiling = scope_role_ceiling(principal)`:
    - User session (no `api_key_id` and no `"scopes"`): `"owner"` (team grant alone decides).
    - Token/API key (`api_key_id` or `"scopes"` present): `scopes = set(principal.get("scopes") or ())`:
      - If `"task:merge"` or `"admin"` in `scopes` -> `"admin"`
      - Else if `{"project:write", "task:write", "plan:write", "mcp:write"} & scopes` (or bare `"api"` when no narrower read/write scope is present) -> `"member"`
      - Else if `"mcp:read"` in `scopes` (or read-only scopes) -> `"viewer"`
      - Else -> `None` (raise `403`).
  - `effective_role = min(grant_role, ceiling, key=lambda r: ROLE_LEVELS[r])`.
  - If `ROLE_LEVELS[effective_role] < ROLE_LEVELS[min_role]`, `await db.rollback()`, audit, and raise `HTTPException(403, f"Requires {min_role} role on project")`.
  - Return `ProjectAccess(project_id=project_id, org_id=project_org_id, role=effective_role, user_id=principal["id"])`.

### 2. Indirect project resolution helpers (`project_authz.py` & `pty/access.py`)

- **By filesystem path (`require_path_project_role(principal, path, min_role, db)`):** Resolve `project_id = project_id_for_path(path)` (matching project roots and `.pfactory/worktrees` paths against `load_projects()`). If `project_id is None`, raise `404`; otherwise delegate to `require_project_role`.
- **By repository slug (`require_repo_project_role(principal, repo, min_role, db)`):** Match normalized `owner/repo` against `load_projects()` (`github_repo` or git remote). If matched to one or more registered projects, require `min_role` on a visible matching project (raising `404` if matched only to invisible projects); if the repo is not registered as a project, fall back to `creator_org` membership for intake or `404` for project operations.
- **By bare `spec_id` (`pfactory_tasks.py`):** Scan `~/.pfactory/workspaces/<project_id>/specs/<spec_id>` across projects where `require_project_role(principal, project_id, min_role)` succeeds; if none is visible, raise `404` (and if visible but under-roled, `403`).
- **List filtering (`visible_project_ids(principal, db, min_role="viewer")`):** Batch-load the principal's `OrgMember` rows in one query and filter `load_projects()` in memory so list endpoints never N+1 query Postgres.

### 3. Complete Inventory of Project-Scoped Surfaces

| Path / Surface | How Owning Project Is Resolved | Required Role |
|---|---|---|
| `GET /api/projects` | Filter `load_projects()` via `visible_project_ids` | `viewer` (filter) |
| `POST /api/projects`, `POST /api/projects/scan` | `creator_org(principal, db)` / filter foreign registered paths | `member` (in org) |
| `GET /api/projects/{project_id}`, `/version`, `/worktrees`, `/tasks`, `/tasks/{spec_id}/logs`, `/mcp-status` | Path `project_id` | `viewer` |
| `POST /api/projects/{project_id}/tasks/{spec_id}/logs/watch`, `/unwatch` | Path `project_id` | `viewer` |
| `POST /api/projects/{project_id}/initialize`, `/tasks`, `/tasks/archive`, `/tasks/unarchive` | Path `project_id` | `member` |
| `PUT /api/projects/{project_id}`, `DELETE /api/projects/{project_id}`, `PATCH /api/projects/{project_id}/settings` | Path `project_id` | `admin` |
| `GET /api/projects/{projectId}/auto-fix/config`, `/queue` | Path `projectId` | `viewer` |
| `PUT /api/projects/{projectId}/auto-fix/config`, `POST .../check-new`, `POST .../{issueNumber}/start` | Path `projectId` | `member` |
| `GET /api/projects/{projectId}/changelog`, `/branches`, `/tags`, `POST .../done-tasks`, `/specs`, `/suggest-version`, `/suggest-version-commits`, `/commits-preview` | Path `projectId` | `viewer` |
| `POST /api/projects/{projectId}/changelog/generate`, `/save`, `/images` | Path `projectId` | `member` |
| `GET /api/projects/{projectId}/context`, `/memory/status`, `/memory/search`, `/memory/recent`, `/env`, `/claude-auth` | Path `projectId` | `viewer` |
| `POST /api/projects/{projectId}/context/refresh`, `/claude-setup` | Path `projectId` | `member` |
| `PATCH /api/projects/{projectId}/env` | Path `projectId` | `admin` |
| `GET /api/projects/{projectId}/insights/providers`, `GET .../insights`, `GET .../insights/sessions` | Path `projectId` | `viewer` |
| `POST /api/projects/{projectId}/insights/message`, `/stop`, `/create-task`, `/generate-task`, `/sessions`, `/sessions/{id}/switch`, `PATCH .../sessions/{id}`, `PATCH .../model`, `DELETE .../insights`, `DELETE .../sessions/{id}`, `DELETE /api/projects/{projectId}/files/insights` | Path `projectId` | `member` |
| `GET /api/projects/{projectId}/releases/versions`, `POST .../releases/preflight` | Path `projectId` | `viewer` |
| `POST /api/projects/{projectId}/git/squash`, `/git/worktree`, `POST .../releases` | Path `projectId` | `member` |
| `GET /api/projects/{projectId}/github/repositories`, `/status`, `/issues`, `/issues/{n}`, `/issues/{n}/comments`, `/prs`, `/prs/{n}/review`, `/prs/{n}/new-commits`, `/prs/{n}/logs` | Path `projectId` | `viewer` |
| `POST /api/projects/{projectId}/github/issues/{n}/investigate`, `/import`, `/issues/{n}/close`, `/prs/{n}/review`, `DELETE .../review`, `POST .../post-review`, `/comment`, `/approve`, `/assign`, `/cancel` | Path `projectId` | `member` |
| `POST /api/projects/{projectId}/github/prs/{n}/merge` | Path `projectId` | `member` (`task:merge` ceiling -> `admin` for tokens) |
| `GET /api/git/branches`, `/current-branch`, `/main-branch`, `/status` (`?path=`) | `project_id_for_path(path)` | `viewer` |
| `POST /api/git/init` (`body.path`) | `project_id_for_path(body.path)` (if inside registered project) | `member` |
| `GET /api/github/detect-repo` (`?path=`) | `project_id_for_path(path)` | `viewer` |
| `POST /api/github/remote` (`body.projectPath`) | `project_id_for_path(body.projectPath)` | `member` |
| `GET /api/github/auto-detect` (`?projectId=`), `POST /api/github/persist-token` (`body.projectId`) | Query/body `projectId` (when provided) | `admin` |
| `GET /api/github/copilot/pr`, `GET /api/github/branches` (`?repo=`) | `require_repo_project_role(repo)` | `viewer` |
| `POST /api/github/copilot/dispatch`, `POST /api/github/prs/{pr_number}/plan-review` (`body.repo`) | `require_repo_project_role(body.repo)` | `member` |
| `GET /api/files/list`, `GET /api/files/read` (`?path=`), `GET /api/files/serve` (`?path=&root=`) | `project_id_for_path(path / root)` | `viewer` |
| `GET /api/files/discover` (`?base_path=`) | Filter out paths registered to non-visible projects | `viewer` (filter) |
| `GET /api/tasks` (`?project_id=`), `GET /api/tasks/running` | Filter by `visible_project_ids` (or check `project_id`) | `viewer` (filter) |
| `GET /api/tasks/{task_id}`, `/qa-report`, `/plan-html`, `/logs`, `/status`, `/running`, `/files`, `/diff`, `/git-status`, `/merge-preview`, `/subtasks/{id}/logs`, `/worktree/status`, `/worktree/diff`, `/worktree/Log` | `parse_task_id(task_id)` -> `project_id` | `viewer` |
| All mutating `/api/tasks/{task_id}/*` (`PUT`, `PATCH`, `DELETE`, `/start`, `/stop`, `/restart`, `/plan`, `/qa`, `/recover`, `/archive`, `/unarchive`, `/feedback`, `/review`, `/subtasks`, `/dependencies`, `/worktree/*`) | `parse_task_id(task_id)` -> `project_id` | `member` (`create-pr` & `merge` require `task:merge` token scope) |
| `POST /api/tasks/worktree/open-in-ide`, `POST /api/tasks/worktree/open-in-terminal` (`body.worktreePath`) | `project_id_for_path(body.worktreePath)` | `member` |
| `POST /api/tasks/{spec_id}/agent-console/attach`, `/detach`, `WS .../agent-console/ws` | `resolve_spec(spec_id)` -> `project_id` | `member` (HTTP), `viewer` (WS) |
| `GET /api/pfactory/tasks` | Filter workspace dirs by `visible_project_ids` | `viewer` (filter) |
| `GET /api/pfactory/tasks/{spec_id}`, `/verdicts.json`, `/triage-report.*`, `/test-plan.json`, `/pr-comment-body.md`, `/catalog`, `/evidence/{test_id}/{artifact}`, `/visual-baselines*`, `WS /api/pfactory/tasks/{spec_id}/logs/stream` | Workspace parent `workspaces/<project_id>/specs/<spec_id>` | `viewer` |
| `POST /api/pfactory/tasks/{spec_id}/merge`, `/dismiss`, `/visual-baselines/{target}/{snapshot}/accept` | Workspace parent `workspaces/<project_id>/specs/<spec_id>` | `member` (`merge` requires `task:merge` token scope) |
| `GET /api/plan/sessions` | `plan_session_filter` (by `session.project_id` / `session.org_id`) | `viewer` (filter) |
| `GET /api/plan/sessions/{session_id}`, `/audit-pack`, `GET /api/plan/{session_id}/questions` | `session.project_id` (via `require_project_role`) or `session.org_id` | `viewer` |
| `POST /api/plan/sessions/ingest-text`, `/from-issue`, `/ingest` (`body.project_id`, `body.repo`), `POST /api/plan/sessions/{session_id}/*`, `POST /api/plan/intake`, `POST /api/plan/{session_id}/answer` | `body.project_id` / `session.project_id` / `require_repo_project_role(repo)` | `member` |
| `GET /api/terminals`, `/sessions`, `/worktrees` (`?project_id=` / `?project_path=`) | Filter via `pty/access.py` (`require_project_role`) | `viewer` |
| `POST /api/terminals`, `DELETE /api/terminals/sessions`, `POST /api/terminals/worktrees`, `DELETE /api/terminals/worktrees/{name}`, `POST /api/terminals/restore*`, `GET/DELETE/POST /api/terminals/{terminal_id}*`, `WS /ws/terminal/{terminal_id}` | `session.project_id` or `project_id_for_path(project_path)` | `member` (mutations/WS), `viewer` (`GET`) |
| `WS /ws/tasks/{task_id}/logs`, `WS /ws/tasks/{task_id}/progress`, `WS /ws/events` | `parse_task_id(task_id)` -> `project_id` / event `project_id` filter | `viewer` |
| MCP tools (`mcp_stdio/router.py`, `mcp_remote/tools.py`, `routes/mcp_rpc.py`) | Project / task / plan target + `key.scopes` ceiling | `viewer` (`mcp:read`), `member` (`mcp:write`/`task:write`/`project:write`/`plan:write`), `admin` (`task:merge`) |

### 4. Generated route-table authorization test (`apps/web-server/tests/test_project_authz_routes.py`)

Walk `app.routes` (both `APIRoute` and ` APIWebSocketRoute`) plus the MCP tool registries (`mcp_stdio/server.py::TOOLS`, `mcp_remote/tools.py::TOOLS`):
- Classify every route as either explicit public/system/auth/org-admin allowlisted (exact `(method, path)` set for `/api/health`, `/api/auth/*`, `/api/orgs/*`, `/api/settings/*`, `/api/usage/*`, etc.) or project-scoped.
- Assert every project-scoped route declares a `ProjectRoleGuard` dependency (or registers in the verified indirect-guard registry exercised by parametrized 404/403 tests). Any newly added project route without `require_project_role` fails CI immediately.

## Alternatives rejected

- **Middleware regex matching on URLs:** Rejected because indirect routes (`/api/files/read?path=...`, `/api/pfactory/tasks/{spec_id}`, `/api/plan/sessions/{session_id}`) resolve their owning project from query parameters, workspace directories, or database rows rather than a URL `{project_id}` segment.
- **Returning 403 for non-members:** Rejected because the shared Factory#3857 contract mandates 404 whenever a project is unknown or invisible to the caller.

## Risks

- **Cluster and `parr-run` jobs using `APP_API_TOKEN`:** Mitigated by defaulting `LEGACY_SHARED_TOKEN_FULL_ACCESS = True` in phase 2 and logging a deprecation warning on each use.
- **Existing tests expecting 200 or running without auth headers:** `DISABLE_AUTH=True` (and the test fixture legacy token while `LEGACY_SHARED_TOKEN_FULL_ACCESS=True`) resolves the seeded `"default"` org with `owner` role so non-RBAC tests continue passing unchanged.

## Verification

- `apps/web-server/.venv/bin/pytest apps/web-server/tests/test_project_authz_routes.py apps/web-server/tests/test_rbac_task_routes_1061.py apps/web-server/tests/test_logsafe_vendored.py -v`
- `apps/backend/.venv/bin/pytest tests/test_ratchet_staged.py -v`
