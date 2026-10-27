---
status: draft
issue: 939
intent: intent/2026-10-27-939-task-route-rbac.md
---

# Spec: org role checks on task and plan-pipeline write routes, shadow mode first

## Design

1. **New `apps/web-server/server/routes/project_authz.py`**, ported from
   AIFactory `origin/dev` `routes/project_authz.py` (post-#1911/#1919):
   `is_service_principal(user)` returns `bool(user.get("is_service"))` only (a
   JWT `role` claim never counts); `check_project_access(user, project,
   membership, minimum_role)` (401 no user, allow service, 404 no project, 403
   no `org_id`, 403 key bound to another org, 403 not a member, 403 below
   minimum); `authorize_project_for_user`; `TaskAccessChecker` /
   `require_task_access(minimum_role)` (project id = text before the first `:`
   of `task_id`); `_audit_authz_denial` on its own committed session via
   `log_audit_event_bg`. PF's own `ROLE_LEVELS`/`_role_level`
   (`routes/organizations.py:39-51`), `load_projects`
   (`services/project_paths.py:32`) and `is_service` marker (`auth.py:291`,
   `:356`, #942). `DISABLE_AUTH` allows, as in AIF.
2. **Plan sessions have no project or org.** `plan.service` sessions carry
   only `tenant_id`; no plan-pipeline write route takes a task or project id
   (the hub plan assumed they did). So add `PlanAccessChecker` /
   `require_plan_access(minimum_role)`: service principal allows; an `acw_`
   key (`api_key_id`) needs the minimum role in the key's `org_id`; a JWT
   user needs the minimum role in at least one org; otherwise 403. Binding a
   session to one org (stamp `org_id` at intake) belongs to S6.
3. **Flag** `RBAC_ENFORCE_TASK_ROUTES: bool = False` in `server/config.py`
   (same name as TFactory). Both checkers share one `_decide` path:
   - On: AIF behaviour. A 403 is audited and raised; 401/404 are raised.
   - Off (shadow): run the same decision. Any `HTTPException` (403s, the 404
     for an unknown project, the 403 for a project without `org_id`) is
     caught, logged as `rbac.would_deny` at WARNING with route, principal id,
     project id, status and detail, audited once in `_audit_authz_denial`'s
     shape with `details.shadow = true` and `details.status`, and the request
     proceeds. Any other exception (DB, `projects.json` read) is logged at
     WARNING and the request proceeds. Shadow never changes a response.
   - Audit actions `authz.denied` and `gate.rejected` (member below minimum)
     are added to `services/audit_service.py` as `ACTION_AUTHZ_DENIED` /
     `ACTION_GATE_REJECTED`, matching AIF.
4. **Wiring** (`dependencies=[Depends(...)]` on the decorator, so handler
   signatures stay unchanged):
   - `require_task_access("member")` on every `POST/PUT/PATCH/DELETE` route
     with a `{task_id}` path param in `routes/execution.py` (start `:108`,
     stop `:550`, recover `:586`) and `routes/tasks.py` (`:1273`, `:1298`,
     `:1376`, `:1426`, `:1427`, `:1568`, approve-plan `:1622`, reject-plan
     `:1762`, `:2538`, `:2810`, `:3064`, `:3335`, `:3463`, merge `:3754`,
     `:4220`), except `logs/watch` `:2179` and `logs/unwatch` `:2190`, which
     subscribe to a read stream and take `viewer`.
   - `require_plan_access("member")` on all 14 write routes of
     `routes/plan_pipeline.py` (`:195`, `:219`, `:251`, `:349`, `:405`,
     `:430`, `:449`, `:463`, `:486`, `:508`, `:520`, `:538`, `:628`, `:664`).

## Alternatives rejected

- **Enforce now.** Locks every human out: no PF project has an `org_id`
  (`routes/projects.py` never writes one).
- **Router-level `dependencies=`** on the tasks/execution routers: also hits
  GETs and the non-task routes, changing the minimum for reads.
- **Bind plan sessions to the tenant as an org.** `tenant_id` is the
  Keycloak tenant claim, not an `Organization.id`; mapping them is S6 work.
- **Body-keyed creates** (`POST /api/tasks`, `/api/tasks/create-and-run`)
  and `pfactory_tasks.py` `/{spec_id}/...` routes: not task-keyed (project
  id in the body, or no project at all). Left for a follow-up issue, listed
  in the PR.

## Risks

- Shadow mode adds one `projects.json` read and one indexed `OrgMember`
  query per write request, and an audit row per would-deny. With no project
  owned by an org today, every human write produces one would-deny row;
  acceptable, it is the evidence S6 needs, and writes are low volume.
- A bug in the shadow wrapper could change a response. Guarded by tests that
  compare the response with the dependency to the plain handler.

## Verification

`pytest tests/test_task_route_rbac.py` (new): viewer 403 on start with the
flag on and 200 with it off; member passes; `is_service` passes; key bound
to another org 403; JWT `role: admin` gets no bypass; flag off + unknown
project gives the handler's own response; flag off + lookup raising still
succeeds; plan route viewer 403 on / member pass; every listed route carries
the dependency (fails if one is removed). Full suite green with the flag off.
