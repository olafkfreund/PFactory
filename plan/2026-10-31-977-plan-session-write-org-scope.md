---
status: draft
issue: 977
spec: spec/2026-10-31-977-plan-session-write-org-scope.md
coders:
  1: codex   # lead named codex for this lane
  2: codex
  3: codex
---

# Plan: scope plan-session writes to the session's org

Approved decisions (from the spec):
- Reuse #976's `authorize_plan_session` (`routes/project_authz.py`) with
  `minimum_role="member"`. No new authz helper.
- Rule: org-scoped `acw_` key on another org's / unknown / org-less session →
  404 `unknown session '<id>'`, always (flag off too). Humans: shadow audit row
  with the flag off, 404 under `RBAC_ENFORCE_TASK_ROUTES`. Service principal
  and auth-disabled unchanged. The check runs before the write.
- MCP proxies set `request.state.user = principal` (AuthMiddleware skips
  `/api/mcp-stdio/`).
- `mcp_rpc._resolve_session` is a service principal by design: comment + docs,
  no code change; per-org identity deferred to S6 (olafkfreund/Factory#312).

## Steps

1. `apps/web-server/server/routes/plan_pipeline.py`: replace sync
   `_guard_tenant(session_id, request)` (~line 433) with
   `async def _guard_session(session_id, request, db)` that keeps the tenant
   check, returns early when `SERVICE.get` raises `PlanServiceError`, then
   `await authorize_plan_session(_user(request), session_id, lambda: _session_org(session, user), db, minimum_role="member")`.
   Call `await _guard_session(...)` first in: process, apply_suggestions,
   re_gate, approve, approve_access, waive, reject, discard, delete_session,
   emit (before `_load_docs_connections`/`db.close()`), emit_contract. Add
   `request: Request` and `db: Annotated[AsyncSession, Depends(get_db)]` where
   missing; put `db` before any parameter with a default (process's `updates`).
   → verify by `cd apps/backend && .venv/bin/python -m pytest ../../tests -q -k "plan_pipeline or plan_session or plan_routes"` (no new failures).
   Traps: no `Depends()` call in a default (B008 is ratcheted); keep `cast(PlanService, SERVICE)` at call sites, never module level (tests monkeypatch `pp.SERVICE`).
2. `apps/web-server/server/mcp_stdio/router.py` `proxy_plan_process` /
   `proxy_plan_approve` (~610-640): `principal = _principal(key)`; check;
   `request.state.user = principal` with #976's one-line comment; call
   `plan_process(session_id, request, db=db)` and
   `plan_approve(session_id, ApproveBody(**body), request, db=db)` (keyword
   args matching step 1's signatures).
   → verify by `pytest tests/test_mcp_stdio_proxy.py tests/test_rbac_console_and_mcp.py -q`.
   Traps: none.
3. New `tests/test_plan_session_write_org_scope.py`, reusing the `env` fixture
   shape of `tests/test_plan_session_org_scope.py` (copy it; flag off):
   (a) parametrized over every write route in step 1, an org-B key
   (`X-Key`/`X-Org: B`) on org A's session gets 404 `unknown session '<id>'`,
   the session's `status`/`version` are unchanged and the response does not
   contain the session title; (b) org-B key on org B's session reaches the
   handler (not 404 from authz) for process and approve; (c) a human non-member
   with the flag off is allowed on approve/process past authz and a shadow audit
   row is recorded; under enforce 404; (d) MCP `/api/mcp-stdio/plan/sessions/{id}/process`
   and `/approve` with an org-B `AuthenticatedKey` (scopes incl. `plan:write`
   as `_PlanWrite` requires) give 404 on org A's session. Stub
   `process_async`/emit side effects if they would run real LLM / `gh`.
   → verify by `pytest tests/test_plan_session_write_org_scope.py -q` passing, and failing with steps 1-2 reverted.
   Traps: `ensure_tracked_project` stub as in #976's test.
4. Item 2 (lead does it): comment on `routes/mcp_rpc.py` `_resolve_session`, and
   a paragraph in `docs/dev/rbac-enforce-runbook.md` under "Not affected".
   → verify by reading the diff.

## Tests

`ruff check` + `ruff format --check` on touched files, `python scripts/ratchet_lint.py`,
the pytest commands above. Expected: all green.

## Rollback

Revert the PR. No schema or data change.
