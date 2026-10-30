---
status: approved
issue: 946
intent: intent/2026-10-30-946-mcp-remote-authz.md
---

# Spec: remote MCP tools authorize the calling key, not the service token

## Design

Same as AIFactory #1917, on this repo's helpers.

`apps/web-server/server/mcp_remote/tools.py`:

1. `_principal(key)`: `{"id": key.user_id, "api_key_id": key.key_id, "org_id": key.org_id}`,
   the shape `mcp_stdio` and the REST middleware use. A key with no `user_id`
   becomes `{"id": "default", "is_service": True}` (unchanged behaviour).
2. `_authorize_call(tool_name, arguments, key, minimum_role)` runs after
   `require_scope` in `dispatch_tool_call`. Target: `project_id` for
   `list_tasks`, `task_id` for every other tool except `list_projects`; the
   project is the part of the task id before the first `:`. Only the argument
   the tool proxies is read. The value must be a `str` matching
   `[\w:-][\w.:-]*` (no `/ ? # %`, no leading `.`), and a `task_id` must
   contain `:`; otherwise `MCPAuthError("invalid <arg>")`. Then
   `authorize_project_for_user(_principal(key), project_id, db, minimum_role)`
   (always enforces) with a session from `get_db()`; its `HTTPException`
   becomes `MCPAuthError("<tool> denied: <detail>")`, so `dispatch_tool_call`
   gains no new except branch. If `get_db()` yields nothing, refuse.
   Role: `viewer` for `mcp:read`, `member` for `mcp:write`.
3. `list_projects`: `_visible_org_ids(key)` returns `None` (no filter) for a
   service key or `DISABLE_AUTH`; otherwise `{key.org_id}` when an `OrgMember`
   row for (`key.org_id`, `key.user_id`) exists, else the empty set. The REST
   result is filtered by each project's `org_id` from the same
   `load_projects()` the authz helper reads. One session, one query.

`apps/web-server/server/mcp_remote/server.py`: bind each SSE session to its
key. `sse_endpoint` wraps `send`, reads `session_id=<32 hex>` from the
transport's first `endpoint` body, stores `_session_keys[sid] = key.key_id`,
and drops it when the stream closes. `messages_endpoint` returns 403 unless
`_session_keys.get(session_id) == key.key_id` (unknown session: 403).

## Alternatives rejected

- Forward the caller's key to REST: REST requires the `api` scope, so MCP-only
  keys would fail or need `api`, which opens all of REST.
- Shadow-mode `authorize_project` (as `mcp_stdio` does): remote MCP would stay
  open until `RBAC_ENFORCE_TASK_ROUTES` flips. Remote is off by default, so
  enforcing now locks nobody out.
- Check inside `_call_internal`: it sees only a path, not which argument names
  the project.
- Per-project `authorize_project_for_user` in `list_projects`: one audit row
  and query per hidden project.

## Risks

- Projects without `org_id` deny every non-service key on remote MCP. Remote
  MCP is off everywhere today.
- Ids outside the allowed set are refused. Task ids are `project:spec`, project
  ids are uuids or slugs.
- The session id is read from the wire format of the `endpoint` event; an mcp
  upgrade that changes it makes every POST 403 (fails closed).

## Verification

- `tests/test_mcp_remote_org_authz.py` (real SQLite rows, real
  `authorize_project_for_user`): an org-A member key is denied every task
  tool on an org-B task and `list_tasks` on an org-B project, with
  `_call_internal` never called; succeeds on its own org; `list_projects` hides
  B; a viewer key holding `mcp:write` is denied write tools and allowed reads;
  `a:1/../b:2`, `pa` and non-strings are refused. Fails on dev.
- `tests/test_mcp_remote_session_key.py`: another key's POST is 403, the
  owner's is delivered, an unknown session is 403, the binding is dropped on
  close. Fails on dev.
- `tests/test_mcp_remote.py` passes with the check stubbed.
- CI ratchet (ruff, mypy) run locally as CI runs it; full suite on Postgres in CI.
