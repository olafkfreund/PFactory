---
status: approved
issue: 1099
spec: spec/2026-11-20-1099-pf-project-authz.md
coders:
  1: codex # Core authz bottleneck, scope ceiling, legacy token flag, and default local org seeding
  2: agy   # Project route and sub-router dependency sweep (projects, auto_fix, changelog, context, insights, mcp)
  3: agy   # Indirect filesystem/repo routes (files, git, github)
  4: codex # Task, execution, pfactory bare-spec_id, plan pipeline, terminal/WS, and MCP scope-grant intersection
  5: agy   # Parametrised route-table authz test and matrix regression tests
reviewers:
  - codex
  - agy
---

# Plan: Project-scoped authorization (`require_project_role`), filtered lists, and indirect-path checks

Self-contained summary of approved decisions:
- `require_project_role(principal, project_id, min_role="viewer", *, db)` in `apps/web-server/server/routes/project_authz.py` enforces `ROLE_LEVELS = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}`.
- Raises `404` when `project_id` is unknown, has no `org_id` (outside single-user default mode), or the principal has no `>= viewer` grant on the owning org (including cross-org `acw_` keys). Raises `403` when the project is visible (`grant >= viewer`) but `effective_role < min_role`.
- `effective_role = min(team_grant_role, scope_role_ceiling(principal))`. Scope ceilings for token principals: `mcp:read` -> `viewer`; `project:write | task:write | plan:write | mcp:write` (or bare `api` without narrower scopes) -> `member`; `task:merge | admin` -> `admin`. User sessions (OIDC/JWT, no `api_key_id`) have no scope ceiling (`owner`).
- `LEGACY_SHARED_TOKEN_FULL_ACCESS: bool = True` (`APP_LEGACY_SHARED_TOKEN_FULL_ACCESS`) in `config.py` controls whether `APP_API_TOKEN` gets `is_service=True` full access (with a deprecation warning log on each use). When `False`, `APP_API_TOKEN` is rejected (`401/403`). Scoped `acw_` keys never bypass. Single-user mode (`DISABLE_AUTH=True`) seeds and uses a `"default"` org where `"default"` user is `owner`.
- Lists (`GET /api/projects`, `GET /api/tasks`, `GET /api/tasks/running`, `GET /api/pfactory/tasks`, `GET /api/plan/sessions`, `GET /api/terminals`) filter to projects with `>= viewer`.

## Steps

1. `apps/web-server/server/config.py:41-50`, `apps/web-server/server/auth.py:125-205,300-375,470-505`, `apps/web-server/server/database/engine.py:241-262`, `apps/web-server/server/routes/project_authz.py:1-555`:
   - Add `LEGACY_SHARED_TOKEN_FULL_ACCESS: bool = True` and set `RBAC_ENFORCE_TASK_ROUTES: bool = True` in `config.py`.
   - In `auth.py::_try_authenticate_personal_key`, attach `"scopes": tuple(scopes)` to the returned user dict. In `auth.py::verify_token`, `AuthMiddleware.dispatch`, and `authenticate_websocket`, gate `_is_legacy_api_token` on `settings.LEGACY_SHARED_TOKEN_FULL_ACCESS` and emit a deprecation warning log on use.
   - In `database/engine.py::init_db`, when `settings.DISABLE_AUTH` is `True`, ensure `Organization(id="default", name="Default", slug="default", owner_id="default")` and `OrgMember(org_id="default", user_id="default", role="owner")` exist.
   - In `routes/project_authz.py`, implement `scope_role_ceiling`, `require_project_role`, `visible_project_ids`, `require_path_project_role`, and `require_repo_project_role`; update `check_project_access`, `_AccessChecker` subclasses, `authorize_project`, `authorize_project_for_user`, `authorize_plan_session`, and `authorize_plan_ingest` to delegate to `require_project_role` (raising `404` when invisible, `403` when visible but under-roled, and calling `await db.rollback()` before `_audit_denial`).
   → verify by `apps/web-server/.venv/bin/pytest apps/web-server/tests/test_rbac_task_routes_1061.py -v`
   Traps: `#1936` DB pool exhaustion — always `await db.rollback()` before spawning background audit tasks; ruff `B008` lint ratchet — use `Annotated[..., Depends(...)]`.

2. `apps/web-server/server/routes/projects.py:385-1460`, `apps/web-server/server/routes/auto_fix.py:64-138`, `apps/web-server/server/routes/changelog.py:167-792`, `apps/web-server/server/routes/context.py:93-715`, `apps/web-server/server/routes/insights.py:74-355`, `apps/web-server/server/routes/mcp.py:71-140`:
   - Filter `GET /api/projects` in `projects.py:385-395` using `visible_project_ids(principal, db)`. Guard `POST /api/projects/scan` with org membership and filter out directories registered to foreign orgs.
   - Add `Depends(require_project_access("viewer"))` to `get_project`, `check_project_version`, `list_project_worktrees`, `list_project_tasks`, `get_project_task_logs`, and `get_project_mcp_status`; `require_project_access("member")` to `initialize_project`; `require_project_access("admin")` to `update_project`, `remove_project`, and `update_project_settings`.
   - Add `require_camel_project_access("viewer")` to `auto_fix.py` (`get_config`, `get_queue`), `changelog.py` read routes, `context.py` read routes (`get_project_context`, `get_memory_status`, `search_memory`, `get_recent_memories`, `get_project_env`, `check_claude_auth`), and `insights.py` read routes (`get_insights_providers`, `get_insights_session`, `list_insights_sessions`).
   - Add `require_camel_project_access("member")` to `changelog.py` write routes (`generate`, `save`, `upload_changelog_image`), `context.py` (`refresh_project_context`, `setup_claude_oauth`), and `insights.py` mutation routes (`send_insights_message`, `stop_insights_stream`, `clear_insights_session`, `generate_task_from_insight`, `create_new_insights_session`, `switch_insights_session`, `delete_insights_session`, `rename_insights_session`, `update_session_model`, `clear_files_insights_session`); add `require_camel_project_access("admin")` to `context.py::update_project_env`.
   → verify by `apps/web-server/.venv/bin/pytest apps/web-server/tests/test_file_based_endpoints.py -v`
   Traps: `changelog.py` and `context.py` are mounted under `/{projectId}` in `projects.py:62,67` — parameter name is camelCase `projectId`.

3. `apps/web-server/server/routes/files.py:132-386`, `apps/web-server/server/routes/git.py:35-120,1060-1445`, `apps/web-server/server/routes/github.py:435-525,738-785,992-1115,1407-2479`:
   - In `files.py`, resolve `project_id_for_path` in `list_directory`, `read_file`, and `serve_file` and enforce `require_project_role(principal, project_id, "viewer")` (updating `serve_file` token resolution to support `acw_` keys and `request.state.user`). Filter foreign registered project directories from `discover_projects`.
   - In `git.py`, enforce `require_path_project_role(..., "viewer")` on `get_git_branches`, `get_current_branch`, `get_main_branch`, and `get_git_status`; enforce `"member"` on `initialize_git` when path belongs to a registered project; add `require_camel_project_access` to `project_router` (`squash_commits`, `manage_worktree` -> `member`) and `releases_router` (`versions`, `preflight` -> `viewer`; `create_release` -> `member`), fixing `settings.projects_file` lookup to `load_projects()`.
   - In `github.py`, enforce `require_project_role` on `auto_detect_token` and `persist_token` when `projectId` is supplied (`admin`), `require_path_project_role` on `detect_github_repo` (`viewer`) and `add_git_remote` (`member`), `require_repo_project_role` on `copilot/dispatch`, `copilot/pr`, `prs/{pr_number}/plan-review`, and `branches`, and `require_camel_project_access` across all routes on `github.project_router` (`viewer` on `GET`, `member` on `POST`/`DELETE`, and `task:merge` ceiling on `POST /prs/{prNumber}/merge`).
   → verify by `apps/web-server/.venv/bin/pytest apps/web-server/tests/test_confine_to_workspace_335.py apps/web-server/tests/test_repo_only_path_647.py apps/web-server/tests/test_repo_only_path_655.py -v`
   Traps: Do not touch vendored `apps/backend/runners/github/` or `apps/web-server/factory_common/`.

4. `apps/web-server/server/routes/tasks.py:1083-1135,1851-2085,3453-3760,4438-4510`, `apps/web-server/server/routes/execution.py:77-105`, `apps/web-server/server/routes/pfactory_tasks.py:175-865`, `apps/web-server/server/routes/plan_pipeline.py:240-480,898-980`, `apps/web-server/server/routes/terminal.py:336-350,730-745`, `apps/web-server/server/websockets/task_access.py:20-75`, `apps/web-server/server/websockets/events.py:275-305`, `apps/web-server/server/mcp_stdio/auth.py:120-130`, `apps/web-server/server/mcp_stdio/router.py:95-210`, `apps/web-server/server/mcp_remote/tools.py:90-170`:
   - In `tasks.py` and `execution.py`, filter `list_tasks` and `get_running_tasks` via `visible_project_ids`; add `require_task_access("viewer")` to `get_task`, `get_qa_report`, `get_plan_html`, `get_task_logs`, `get_task_execution_status`, and `is_task_running`; guard `open_worktree_in_ide` and `open_worktree_in_terminal` with `require_path_project_role(..., "member")`; require `task:merge` scope ceiling on `create-pr` and `merge` for token principals.
   - In `pfactory_tasks.py`, filter `list_tasks` by `visible_project_ids`, add `SpecAccessChecker("viewer")` to all bare-`spec_id` GET endpoints, authenticate + authorize `WS /{spec_id}/logs/stream`, and filter `_find_spec_dir` to visible projects.
   - In `plan_pipeline.py`, validate `body.project_id`, `session.project_id`, and `body.repo` against `require_project_role` / `require_repo_project_role` on `ingest-text`, `from-issue`, `ingest`, `emit`, `emit-contract`, `get_session`, `audit_pack`, and `_guard_session`.
   - In `terminal.py`, guard `restore`, `restore-date`, `invoke-claude`, and `resume-claude` via `_owned` / `require_project_role`. In `websockets/task_access.py` and `websockets/events.py`, remove the shadow-mode JWT bypass.
   - In `mcp_stdio/auth.py`, gate `_is_legacy_api_token` on `LEGACY_SHARED_TOKEN_FULL_ACCESS`. In `mcp_stdio/router.py` and `mcp_remote/tools.py`, pass `"scopes": tuple(key.scopes)` in `_principal(key)` and filter project/task lists through `visible_project_ids`.
   → verify by `apps/web-server/.venv/bin/pytest apps/web-server/tests/test_mcp_stdio_auth.py apps/web-server/tests/test_mcp_remote.py -v`
   Traps: WebSocket denials must close with code `4404` (not visible) or `4403` (under-roled) rather than unhandled HTTPExceptions after accept.

5. `apps/web-server/tests/test_project_authz_routes.py` (new) and `apps/web-server/tests/test_rbac_task_routes_1061.py`:
   - Create `test_project_authz_routes.py` walking `app.routes` (REST + WebSocket) and MCP tool tables (`mcp_stdio` and `mcp_remote`), asserting every non-public route has project authorization wired, and testing the full contract matrix: unknown project -> `404`, non-member project -> `404`, `viewer` on `member` route -> `403`, `member` on `admin` route -> `403`, `owner` grant + `mcp:read` token scope -> effective `viewer` (`403` on write), filtered lists, indirect paths (`/api/files/read`, `/api/pfactory/tasks/{spec_id}`, `/api/plan/sessions`), and `LEGACY_SHARED_TOKEN_FULL_ACCESS` toggle (`True` warns + allows; `False` rejects).
   - Update `test_rbac_task_routes_1061.py` for always-on enforcement and `404` on non-member access.
   → verify by `apps/web-server/.venv/bin/pytest apps/web-server/tests/test_project_authz_routes.py apps/web-server/tests/test_rbac_task_routes_1061.py -v`
   Traps: Ensure test fixtures clean up seeded `projects.json` entries and SQLite rows.

## Tests

```bash
apps/web-server/.venv/bin/pytest apps/web-server/tests/test_project_authz_routes.py apps/web-server/tests/test_rbac_task_routes_1061.py apps/web-server/tests/test_mcp_stdio_auth.py apps/web-server/tests/test_mcp_remote.py apps/web-server/tests/test_logsafe_vendored.py -v
apps/backend/.venv/bin/pytest tests/test_ratchet_staged.py -v
```

## Rollback

Set `APP_LEGACY_SHARED_TOKEN_FULL_ACCESS=true` (default) to keep legacy token automation unblocked, or revert the branch commits to restore shadow mode.
