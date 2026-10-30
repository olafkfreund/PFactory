---
status: approved
issue: 946
spec: spec/2026-10-30-946-mcp-remote-authz.md
coders:
  1: agy
  2: agy
  3: agy
---

# Plan: remote MCP tools authorize the calling key, not the service token

Decisions (from the spec):
- Check in `dispatch_tool_call` after `require_scope`, before any proxy. The
  loopback keeps the service token; the key is not forwarded.
- Principal: `{"id": user_id, "api_key_id": key_id, "org_id": org_id}`;
  no `user_id` → `{"id": "default", "is_service": True}`.
- Target: `project_id` for `list_tasks`, `task_id` (part before first `:`) for
  the rest; `list_projects` is filtered instead. Ids match `[\w:-][\w.:-]*`;
  task ids need `:`.
- `authorize_project_for_user` (always enforcing), `viewer` for reads,
  `member` for writes, session from `get_db()`; no session → refuse.
  `HTTPException` → `MCPAuthError` inside the helper.
- `list_projects`: filter by `load_projects()[id]["org_id"]` in
  `_visible_org_ids(key)` (None for service / `DISABLE_AUTH`, `{key.org_id}` if
  the owner is an `OrgMember` there, else empty).
- SSE session bound to the opening key; `POST /messages/` 403 otherwise.

Repo traps (all steps): run ruff/mypy exactly as ``.github/workflows/cq-ratchet.yml` (`scripts/ratchet_lint.py` and `scripts/mypy_strict_packages.py`)` does in CI
(TID252 relative imports beyond parent, B008, PLR0913/PLR0915, mypy); no
`except` that leaves a variable unset and no mixed implicit/explicit returns
(CodeQL); do not open a second DB session/connection per call; in tests get
modules with `importlib.import_module` and patch module attributes, never
re-import or reload.

## Steps

1. `apps/web-server/server/mcp_remote/tools.py`: add `_SAFE_ID`, `_principal`,
   `_authorize_call`, `_visible_org_ids`; call `_authorize_call` in
   `dispatch_tool_call` inside the existing `require_scope` try; filter
   `list_projects`; update the module docstring (step 1 of the wrapper list).
   `tests/test_mcp_remote.py`: task ids `t1` → `p1:001`, and an autouse fixture
   that stubs `tools._authorize_call` (AsyncMock) and `tools._visible_org_ids`
   (returns None) so those unit tests stay DB-free.
   → verify: `pytest tests/test_mcp_remote.py -q`
2. `apps/web-server/server/mcp_remote/server.py`: `_session_keys`,
   `_SESSION_ID = re.compile(rb"session_id=([0-9a-f]{32})")`, wrapped `send`
   in `sse_endpoint`, key check in `messages_endpoint`. New
   `tests/test_mcp_remote_session_key.py` (port of AIFactory's).
   → verify: `pytest tests/test_mcp_remote_session_key.py tests/test_mcp_remote.py -q`
3. New `tests/test_mcp_remote_org_authz.py`: SQLite engine, users/orgs/members
   as in ``tests/test_rbac_console_and_mcp.py``, `tools.get_db` patched to the SQLite session,
   `load_projects` patched to `{pa: A, pb: B}`, settings `DISABLE_AUTH=False`,
   `_call_internal` mocked; cases from the spec's Verification.
   → verify: new file passes here and fails against `origin/dev`'s `tools.py`
   (checked in a scratch copy, never by stashing). Then `pytest tests -k mcp -q`.

## Tests

`pytest tests -k "mcp or rbac" -q`, the CI ratchet commands locally, CI full
suite (Postgres).

## Rollback

Revert the PR. Remote MCP is off by default.
