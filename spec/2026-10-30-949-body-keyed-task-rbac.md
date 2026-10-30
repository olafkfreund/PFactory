---
status: approved
issue: 949
intent: intent/2026-10-30-949-body-keyed-task-rbac.md
---

# Spec: role checks on the task writes #950 could not key on a path

## Design

All new checks reuse the one decision in `apps/web-server/server/routes/project_authz.py`
(`_project_decision` / `PlanAccessChecker._decide` + `_handle_denial`), so shadow
vs enforce stays one switch.

1. **Core (`project_authz.py`).**
   - Split `_AccessChecker._authorize(key, request, db)` into a public
     `check(user, key, db)` that takes the principal directly; `_authorize`
     reads `request.state.user` and calls it.
   - `check` wraps the decision in `try/.../finally:` with
     `with contextlib.suppress(Exception): await db.rollback()`, so the request
     connection is released on the allow path too (TFactory #1458 item 1). The
     rollback in the generic `except` becomes redundant and goes. `_handle_denial`
     keeps its rollback before the audit (one connection at a time).
   - `authorize_project(user, project_id, db, minimum_role)`: module function,
     `ProjectAccessChecker(minimum_role).check(...)`. For in-handler callers
     (rmux, MCP proxies). Shadow/enforce like the dependencies, unlike the
     always-enforcing `authorize_project_for_user`.
   - `CamelProjectAccessChecker`: `__call__(self, projectId: str, ...)` for the
     camelCase routes.
   - `BodyProjectAccessChecker`: reads `project_id` from `await request.json()`
     (Starlette caches the body, so the handler still gets it; unparseable body
     → key `None` → the decision 404s, shadow logs it). A route dependency, so
     `create_task`'s signature and its direct callers do not change.
   - `require_camel_project_access`, `require_body_project_access` factories, like the others.
2. **REST routes, all `member`:**
   - `routes/tasks.py` `POST ""`: `Depends(require_body_project_access("member"))`.
   - `routes/execution.py` `POST /create-and-run`: `Depends(require_project_access("member"))`;
     `project_id` is a query param there and `ProjectAccessChecker.__call__`'s
     `project_id: str` binds to it.
   - `routes/auto_fix.py` PUT config, check-new, start; `routes/insights.py`
     `POST /create-task`: `Depends(require_camel_project_access("member"))`.
   - `routes/pfactory_tasks.py` merge, dismiss, accept-baseline: a
     `SpecAccessChecker(_AccessChecker)` in that module, keyed on `spec_id`,
     whose `_decide` resolves `_find_spec_dir(_resolve_workspace_root(), spec_id)`;
     the workspace dir name is the project id (`workspaces/<project_id>/specs/<spec_id>`),
     so it calls `_project_decision` with `spec_dir.parent.parent.name` (unknown
     or invalid spec → `None` → 404). A dependency keeps the handlers sync.
3. **rmux (`rmux/bridge.py`).** attach/detach already take `request` and `db`:
   first line `await authorize_project(request.state.user, _project_of(spec_id), db, "member")`.
   `_project_of` = the part before `:` (the frontend passes `task.id`,
   `project_id:spec_id`); a bare spec id has no project → `None` → 404 decision.
   The WebSocket opens `async with async_session_factory() as db:` after
   `verify_websocket_token`, checks `viewer` with the principal from
   `authenticate_websocket`-style claims (a JWT `sub`; legacy token → service
   principal), and on an enforced denial closes with code 4003.
4. **MCP stdio proxies (`mcp_stdio/router.py`).** `_principal(key)`: an
   `AuthenticatedKey` → `{"id": key.user_id, "api_key_id": key.key_id, "org_id": key.org_id}`
   (the same shape `auth._try_authenticate_api_key` gives, so the "key scoped
   to another org" rule applies); `_LegacyAdminKey` → service principal. Each
   write proxy gains `db: Annotated[AsyncSession, Depends(get_db)]` and calls
   `authorize_project(_principal(key), <project>, db, "member")` before the
   handler: create-and-run (query `project_id`), start/stop/recover/approve-plan/
   create-pr/merge (`task_id.split(":", 1)[0]`). Plan write proxies
   (ingest-text, ingest, process, approve) call
   `PlanAccessChecker("member").check(_principal(key), request.url.path, db)`.
   `proxy_add_project` stays as is: creating a project has no project to check.

## Alternatives rejected

- **In-handler call in `create_task`.** Adds `request`/`db` params and breaks
  `insights.py` and the test that call it directly.
- **Always-enforcing `authorize_project_for_user` for rmux/MCP.** It would 403
  every JWT user today (projects have no `org_id`), i.e. enforce before S6.
- **A second session inside the check** for routes without `db`. Two pooled
  connections per request, the bug #950 fixed.
- **Making `pfactory_tasks` handlers `async`.** Merge runs git synchronously;
  async would block the event loop.
- **Path alias on `ProjectAccessChecker`.** The signature is shared by every
  instance; an alias cannot vary per route.

## Risks

- Shadow mode adds one membership query (only when the project has `org_id`,
  none today) and audit rows for would-deny. No response changes.
- `BodyProjectAccessChecker` parses JSON once more per create; trivial.
- WebSocket: a close code 4003 only in enforce mode.

## Verification

- `tests/test_task_route_rbac.py` extended: allow path rolls back the request
  session; `authorize_project` shadow/enforce; camel and body checkers;
  `SpecAccessChecker` maps spec → project (and unknown spec → 404 decision);
  every new route carries its dependency (extend the inventory test);
  rmux attach denied in enforce / allowed in shadow; an MCP proxy with an `acw_`
  key for org B on an org A task: 403 in enforce, 200 + one shadow audit row in shadow.
- `ruff`, `scripts/ratchet_lint.py` (if present), full backend suite.
