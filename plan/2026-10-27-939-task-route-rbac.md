---
status: draft
issue: 939
spec: spec/2026-10-27-939-task-route-rbac.md
---

# Plan: org role checks on task and plan-pipeline write routes, shadow mode first

Approved decisions, carried over from the spec:
- Port AIFactory `origin/dev` `apps/web-server/server/routes/project_authz.py`
  (read it with `git -C /mnt/code/Source-home/GitHub/AIFactory show
  origin/dev:apps/web-server/server/routes/project_authz.py`): keep
  `is_service_principal` (`is_service` only, never a `role` claim),
  `check_project_access`, `authorize_project_for_user`, `_audit_authz_denial`
  (own session via `log_audit_event_bg`, `db.rollback()` before it),
  `ProjectAccessChecker`, `TaskAccessChecker`, `require_project_access`,
  `require_task_access`. Drop AIF's `accessible_org_ids`/`DEFAULT_ORG_ID` (no
  caller in PF). Use PF's `ROLE_LEVELS` (`routes/organizations.py:39`),
  `load_projects` (`services/project_paths.py:32`), `get_db`
  (`database/engine.py`), `OrgMember` (`database/models`).
- New `PlanAccessChecker` / `require_plan_access(minimum_role)`: no
  path param. Service principal allows. `api_key_id` set: needs the minimum
  in `user["org_id"]`. JWT user: needs the minimum in at least one org
  (highest `OrgMember.role` across their rows). Else 403, audited like the
  task checker with `resource_type="plan_session"`.
- Flag `RBAC_ENFORCE_TASK_ROUTES: bool = False` in `server/config.py`.
  On: AIF behaviour (403 audited and raised; 401/404 raised). Off (shadow):
  same decision; every `HTTPException` is caught, logged at WARNING as
  `rbac.would_deny` (principal id, project or session, status, detail) and
  audited once with `details={"detail", "minimum_role", "shadow": True,
  "status"}`; any other exception logs a WARNING and allows. Shadow never
  changes a response. `DISABLE_AUTH` allows without a decision.
- Audit actions `ACTION_AUTHZ_DENIED = "authz.denied"`,
  `ACTION_GATE_REJECTED = "gate.rejected"` (member below minimum).
- Wiring is `dependencies=[Depends(...)]` on each decorator, handler
  signatures unchanged. `member` everywhere except tasks `logs/watch` and
  `logs/unwatch` (`viewer`).
- Not in scope: `POST /api/tasks`, `POST /api/tasks/create-and-run` (project
  id in the body), `pfactory_tasks.py` `/{spec_id}/...` routes, worktree
  open-in-ide/terminal/detect-tools. Follow-up issue.

## Steps

1. Authz module, flag, audit names.
   - `apps/web-server/server/config.py`: add `RBAC_ENFORCE_TASK_ROUTES: bool =
     False` next to `DISABLE_AUTH` (`:41`) with a one-line why comment
     (Factory#312 S2; off until S6 backfills project `org_id`).
   - `apps/web-server/server/services/audit_service.py` (`:52-91` constants):
     add the two action constants.
   - New `apps/web-server/server/routes/project_authz.py` as above. The
     shadow switch lives in one place used by both checkers.
   → `ruff check` and `ruff format --check` on the touched files; `python -c
     "import server.routes.project_authz"` from `apps/web-server`.
   Traps: lazy-import `load_projects` and audit inside functions if an import
   cycle appears (AIF does). Read the flag via `get_settings()` per request so
   tests can toggle it.

2. Wire the routes.
   - `routes/execution.py`: `:108` start, `:550` stop, `:586` recover →
     `require_task_access("member")`.
   - `routes/tasks.py`: `:1273`, `:1298`, `:1376`, `:1426`, `:1427`, `:1568`,
     `:1622` approve-plan, `:1762`, `:2538`, `:2810`, `:3064`, `:3335`,
     `:3463`, `:3754` merge, `:4220` → `member`; `:2179`, `:2190` → `viewer`.
   - `routes/plan_pipeline.py`: all 14 write routes (`:195`, `:219`, `:251`,
     `:349`, `:405`, `:430`, `:449`, `:463`, `:486`, `:508`, `:520`, `:538`,
     `:628`, `:664`) → `require_plan_access("member")`.
   → `git grep -n '@router\.\(post\|put\|patch\|delete\)("/{task_id}' --
     apps/web-server/server/routes` lists 20 routes, all wired;
     existing tests for these modules still pass.
   Traps: line numbers shift as you edit; match by path. Do not add router-
   level dependencies (would hit GETs).

3. Tests: new `tests/test_task_route_rbac.py` (pattern:
   `tests/test_audit_export_gate.py`, SQLite via aiosqlite, override
   `get_db` on the module's own reference). Cases:
   - viewer 403 on start with the flag on, and passes the dependency with it
     off (assert the handler's response, not a 403);
   - member passes; `is_service` passes;
   - `acw_` key (`api_key_id`, `org_id` = other org) 403 with the flag on;
   - JWT dict with `role: "admin"` and no membership gets 403 with flag on;
   - flag off + unknown project id: response equals the same app without the
     dependency;
   - flag off + `load_projects` raising: request succeeds;
   - shadow mode writes one audit row with `details.shadow` true;
   - plan route: viewer-only user 403 with flag on, member passes;
   - every route in step 2's list carries the dependency with the right
     role (introspect `app.routes` / `route.dependencies`), so removing one
     fails the test.
   → `pytest tests/test_task_route_rbac.py -q`, then the tests for
     execution/tasks/plan_pipeline routes, then the full suite via the
     pre-commit hook.
   Traps: never `git add -A` (the `.venv` symlink is not ignored); hooks run
   on every commit, never `--no-verify`.

## Tests

`pytest tests/test_task_route_rbac.py -q` passes; full suite green with the
flag off; `ruff`, mypy ratchet (`scripts/ratchet_lint.py` if present) clean.

## Rollback

Flag is off by default: merging changes no response. Revert the PR to undo.
Once S6 turns the flag on, set it back to `false` in gitops first.
