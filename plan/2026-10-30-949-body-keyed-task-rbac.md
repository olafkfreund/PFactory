---
status: approved
issue: 949
spec: spec/2026-10-30-949-body-keyed-task-rbac.md
coders:   # the user asked for Codex as a team member this wave
  1: codex
  2: codex
  3: codex
  3b: codex
  4: codex
---

# Plan: role checks on the task writes #950 could not key on a path

Paths are relative to `apps/web-server/server/` unless they start with `tests/`.
All checks run the existing decision in `routes/project_authz.py` and the
existing shadow/enforce switch (`RBAC_ENFORCE_TASK_ROUTES`, default off =
shadow: log `rbac.would_deny` + audit, never change a response). Writes need
`member`; the console WebSocket needs `viewer`.

Repo traps (every step):
- CodeQL: no variable first assigned inside a `try` and read in an `except`/
  after it; no function mixing implicit and explicit returns (end `-> None`
  functions with `return None` where siblings do, as the file already does).
- Never open a second DB session/connection in a check path that already has
  one; the only new session is the WebSocket's own (it has no request session).
- Tests patch through `project_authz`'s own references
  (`pa.project_paths`, `pa.audit_service`, `pa.get_settings`, `pa.get_db`):
  other tests reload `server.*`, so a fresh import may not be the object the
  code reads. Same rule for any new module a test patches (patch through the
  module object the route uses).
- Ruff line length / `# noqa` style as in `project_authz.py`; keep comment density.

## Steps

1. `routes/project_authz.py`: core.
   - Add public `async def check(self, user, key, db) -> dict | None` on
     `_AccessChecker` holding today's `_authorize` body, with the
     `try/except HTTPException/except Exception` wrapped so a `finally:` does
     `with contextlib.suppress(Exception): await db.rollback()`. Drop the now
     redundant rollback in the generic `except` (keep the comment's intent in
     the finally). `_authorize(key, request, db)` becomes
     `return await self.check(getattr(request.state, "user", None), key, db)`.
   - `async def authorize_project(user, project_id, db, minimum_role="viewer") -> None`:
     `await ProjectAccessChecker(minimum_role).check(user, project_id, db)`.
   - `class CamelProjectAccessChecker(_AccessChecker)` with
     `__call__(self, projectId: str, request, db=Depends(get_db))` (`# noqa: N803`).
   - `class BodyProjectAccessChecker(_AccessChecker)`: `__call__(self, request, db)`
     reads `await request.json()` (any exception → `None`), key =
     `body.get("project_id")` if a dict else `None`.
   - Factories `require_camel_project_access`, `require_body_project_access`;
     add all new names to `__all__`.
   - Tests in `tests/test_task_route_rbac.py`: allow path ends with a
     `"rollback"` event (member-a, 200, `make.events[-1] == "rollback"` or
     `"rollback" in events` with no audit); `authorize_project` shadow returns
     and audits once for viewer-a, enforce raises 403; camel route
     `/x/{projectId}/y` and body route (JSON `{"project_id": "p1"}`, plus a
     non-JSON body → shadow 200 with a 404 audit) via `make()` (extend `make`
     with a `kind=` argument rather than copying it).
   → verify by `cd apps/backend && PYTHONPATH=. .venv/bin/pytest ../../tests/test_task_route_rbac.py -q`
     (the root `tests/` command from `.husky/pre-commit`; 21 pass at base).

2. REST routes.
   - `routes/tasks.py` `@router.post("", ...)` (~1170): add
     `dependencies=[Depends(require_body_project_access("member"))]`. Do not
     change `create_task`'s signature (called directly by `routes/insights.py:215`
     and `apps/web-server/tests/test_task_board_store.py`).
   - `routes/execution.py` `@router.post("/create-and-run")` (~700):
     `dependencies=[Depends(require_project_access("member"))]` (`project_id`
     is a query param; the checker's `project_id: str` binds to it).
   - `routes/auto_fix.py` PUT `/{projectId}/auto-fix/config`, POST
     `/{projectId}/auto-fix/check-new`, POST `/{projectId}/auto-fix/{issueNumber}/start`;
     `routes/insights.py` POST `/create-task` (router prefix carries `{projectId}`):
     `dependencies=[Depends(require_camel_project_access("member"))]`.
   - `routes/pfactory_tasks.py`: `class SpecAccessChecker(_AccessChecker)`
     overriding `_decide(user, key, db, ctx)`: `spec_dir = _find_spec_dir(_resolve_workspace_root(), key)`
     if `key` passes `_SPEC_ID_RE`/`_validate_spec_id` (catch its HTTPException → `None`),
     project id = `spec_dir.parent.parent.name` if found else `None`, then
     `await _project_decision(user, project_id, db, self.minimum_role, ctx)`.
     `__call__(self, spec_id: str, request, db)`. Put
     `dependencies=[Depends(SpecAccessChecker("member"))]` on merge (~664),
     dismiss (~745), accept-baseline (~828). Handlers stay sync.
   - Tests: extend the inventory test (`test_every_listed_route_carries_the_dependency`
     and its `n`) with these 9 routes and their checker class names; one test that
     `SpecAccessChecker` resolves `workspaces/p1/specs/s1/status.json` under a
     `PFACTORY_WORKSPACE_ROOT` tmp root to project `p1` (member-a allowed,
     viewer-a 403 in enforce) and an unknown spec gives a 404 decision.
   → verify by the step-1 pytest command plus
     `apps/web-server/tests/test_task_board_store.py` and any existing
     auto_fix / insights / pfactory_tasks / create-and-run tests (`grep -l` them).

3. `rmux/bridge.py`.
   - `def _project_of(spec_id) -> str | None`: the part before `:`, else `None`.
   - `attach` and `detach`: first statement
     `await authorize_project(getattr(request.state, "user", None), _project_of(spec_id), db, "member")`.
   - `agent_console_ws`: replace `verify_websocket_token` with
     `authenticate_websocket` (same accepted tokens; it closes 4001 itself and
     raises `WebSocketAuthError` → `return`). Principal: the returned dict, or
     `{"id": "default", "is_service": True}` when it returns `None` (legacy
     token / auth disabled). Then
     `async with async_session_factory() as db: await authorize_project(principal, _project_of(spec_id), db, "viewer")`
     catching `HTTPException` → `await websocket.accept()` then
     `await websocket.close(code=4003, reason="forbidden")` (closing before accept
     is an HTTP 403 handshake reject under uvicorn and loses the code)
     and `return` (only reachable in enforce mode). Do this before
     `_resolve_state_or_404`. Import `async_session_factory` from
     `..database.engine` at module top as a module reference the test can patch.
   - Tests (new `tests/test_rbac_console_and_mcp.py`, reusing the `env` fixture
     style or importing it): attach with viewer-a: shadow 200-path reached
     (409/404 from the handler is fine; assert one shadow audit), enforce 403;
     `_project_of` cases.
   → verify by pytest on the new test file plus any existing rmux tests
     (`grep -rl rmux tests apps/web-server/tests`).

3b. Revision after the lead's review (cross-project bypass, as TFactory #1465):
   the registry is keyed by bare spec id and `_resolve_state_or_404` falls back
   to the suffix of `project_id:spec_id`, so `p1:<pB spec>` was authorized
   against p1 and then reached pB's session.
   - `rmux/session.py`: `SessionState.project_id: str | None = None`;
     `create_for_task(..., project_id: str | None = None)` stores it.
   - `rmux/integration.py` `create_if_enabled(..., project_id=None)` passes it on.
   - `services/agent_service.py` `start_task_execution` (~1167):
     `_rmux_create(spec_id, project_path, cmd, project_id=_project half of task_id_ or None)`.
   - `rmux/bridge.py` `_resolve_state_or_404(spec_id, user)`: after the lookup,
     unless `is_service_principal(user)`, drop the state (→ 404) when
     `_project_of(spec_id)` is `None` (a bare id has no project binding) or
     `!= state.project_id`. attach, detach and the WebSocket pass their principal.
     All modes: this is a lookup fix, not the shadow-mode role decision.
   - Tests in `tests/test_rbac_console_and_mcp.py`: org-A member on
     `p1:sB` where `sB` belongs to `pB` (org B) gets 404 for attach, detach and
     the WebSocket (close 4004) in enforce mode, while `pB:sB` gets 403/4003; a
     bare `sB` is 404 for a non-service caller and found for the service
     principal; `create_for_task` records `project_id` (fake wrapper). The
     reviewer's repro (`p1:sB` attach must not be 200) is one of these.
   → verify by pytest on the new test file plus `tests/rmux`.

4. `mcp_stdio/router.py`.
   - `def _principal(key) -> dict`: `AuthenticatedKey` →
     `{"id": key.user_id, "api_key_id": key.key_id, "org_id": key.org_id}`;
     otherwise (`_LegacyAdminKey`) `{"id": "default", "is_service": True}`.
   - Add `db: Annotated[AsyncSession, Depends(get_db)]` to
     `proxy_create_and_run_task`, `proxy_start_task`, `proxy_stop_task`,
     `proxy_recover_task`, `proxy_approve_plan`, `proxy_create_pr`,
     `proxy_merge_worktree`, and call
     `await authorize_project(_principal(key), <project>, db, "member")` as the
     first statement (`<project>` = `project_id` for create-and-run, else
     `task_id.split(":", 1)[0]`).
   - Plan write proxies `proxy_plan_ingest_text`, `proxy_plan_ingest_upload`,
     `proxy_plan_process`, `proxy_plan_approve`: same `db`, and
     `await PlanAccessChecker("member").check(_principal(key), request.url.path, db)`.
   - `proxy_add_project` unchanged (no project to check yet).
   - Tests: a proxy call (override `require_acw_scope` result / patch the
     delegated handler through the router module's reference) with an
     `AuthenticatedKey(org_id="B", user_id="member-a")` on task `p1:s`: enforce
     403 and handler not called; shadow handler called + one shadow audit;
     legacy key allowed with no audit.
   → verify by pytest on the new test file plus existing mcp_stdio tests
     (`grep -rl mcp_stdio tests apps/web-server/tests`).

Test command everywhere: `cd apps/backend && PYTHONPATH=. .venv/bin/pytest <paths> -q`
(paths relative to `apps/backend`, e.g. `../../tests/...`, `../web-server/tests/...`).

## Tests

- Targeted pytest per step (above), then the full backend suite locally
  before pushing; all green.
- `ruff check` + `ruff format --check` on touched files; mypy ratchet if the repo has one.

## Rollback

Revert the PR. Shadow mode means no response depended on it.
