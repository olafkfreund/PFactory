---
status: approved
issue: 940
spec: spec/2026-10-27-940-api-key-scope-cap.md
---

# Plan: cap acw_ key scopes by org role at mint and at auth, and store naive UTC times

Approved decisions, carried over from the spec:
- Mint: keys cannot mint keys (403). Below `member` (viewer or unknown role,
  level -1) only `mcp:read` may be requested, else 403. `None`/`[]` allowed.
  `expires_at` stored naive UTC.
- Auth: one helper `cap_by_owner_role(session, row, scopes)` in
  `mcp_remote/auth.py`, membership looked up in `row.org_id` only. No
  membership gives empty, below member gives `scopes & {"mcp:read"}`. MCP
  lookup rejects an empty result; REST caps before the `api` check and stores
  `last_used_at` naive UTC.
- Not in scope: project RBAC (Factory#312), route-level org binding, column
  type change, revoking existing keys. No new roles or scope constants.

## Steps

1. `apps/web-server/server/routes/api_keys.py::create_api_key` (~:134-170):
   add a `request: Request` parameter. After the membership 403, add the
   key-cannot-mint guard (`principal = getattr(request.state, "user", None)`;
   dict with `api_key_id` → 403 "API keys cannot be created with an API key")
   and the viewer cap
   (`_role_level(membership.role) < _role_level("member") and set(body.scopes or ()) - {MCP_READ_SCOPE}`
   → 403 "Scopes beyond 'mcp:read' require the 'member' org role"), each with a
   one-line why comment (Factory#312 / #940). Imports: `_role_level` from
   `.organizations` (or absolute if the ratchet flags it),
   `MCP_READ_SCOPE` from `server.mcp_remote.auth`. `expires_at`:
   `(datetime.now(UTC) + timedelta(...)).replace(tzinfo=None)` with comment
   "Naive UTC: the column is a naive DateTime and asyncpg rejects aware values."
   New test `tests/test_api_key_mint_scope_cap.py`: real per-test SQLite DB
   (copy the fixture shape of `tests/test_mcp_key_lookup.py`), FastAPI app
   with `api_keys.router`, `get_db` and `get_current_user` overridden; users
   `viewer-u` (viewer) and `member-u` (member) in `org-1`. Tests:
   viewer + `["mcp:write"]` 403; viewer + `["mcp:read","task:merge"]` 403;
   viewer + `["mcp:read"]` 201; member + `["task:merge"]` 201; a request whose
   `request.state.user` carries `api_key_id` (set via a tiny middleware in the
   test app) 403.
   → verify: `apps/backend/.venv/bin/python -m pytest tests/test_api_key_mint_scope_cap.py -q`
   Traps: never `git add -A` (`apps/backend/.venv` is an untracked symlink);
   if an import cycles at module load, import lazily inside the function and
   record that as a deviation.

2. `apps/web-server/server/mcp_remote/auth.py`: add
   `async def cap_by_owner_role(session, row, scopes) -> frozenset[str]`
   (docstring: caps stored scopes by the owner's CURRENT role in the key's org,
   same mapping as the mint cap; a demotion or removal takes effect next call).
   Query `select(OrgMember.role).where(OrgMember.org_id == row.org_id, OrgMember.user_id == row.user_id)`.
   Import `OrgMember` from `server.database.models` and `_role_level` from
   `server.routes.organizations` (lazily inside the helper if module-level
   import cycles). In `_lookup_by_digest`, after parsing scopes:
   if `row.user_id`: cap, and if empty raise
   `MCPAuthError("API key has no scopes for its owner's current role")`.
   Add that case to the `authenticate` docstring list. The `MCPAuthError`
   docstring says all raise sites are literals: keep it true and update the count.
   `apps/web-server/server/auth.py::_try_authenticate_api_key` (~:162-170):
   after parsing `scopes`, `scopes = await cap_by_owner_role(session, api_key, scopes)`
   (lazy import next to the existing lazy imports), then the existing
   `API_SCOPE` check. `last_used_at = datetime.now(UTC).replace(tzinfo=None)`.
   Tests:
   - `tests/test_mcp_key_lookup.py` (real DB): add demoted-to-viewer key
     scopes == `{"mcp:read"}`; removed member (delete OrgMember) raises
     "no scopes"; viewer whose key has only `mcp:write` raises "no scopes";
     member of another org only (move the OrgMember row to a second org)
     raises "no scopes".
   - REST real-DB test (new `tests/test_api_key_rest_role_cap.py`, same
     fixture shape, monkeypatch `server.database.engine.get_db` to yield a
     session from the test engine): member key with `api` authenticates;
     after deleting the OrgMember row `_try_authenticate_api_key` returns None;
     demoted to viewer returns None.
   - Update `tests/test_api_key_auth.py` and `tests/test_mcp_remote.py` /
     `tests/test_mcp_stdio_proxy.py` fake sessions so the extra membership
     query returns a `member` role (or monkeypatch `cap_by_owner_role` to
     identity where the file stubs the DB). Don't weaken any assertion.
   → verify: `apps/backend/.venv/bin/python -m pytest tests/test_mcp_key_lookup.py tests/test_api_key_rest_role_cap.py tests/test_api_key_auth.py tests/test_mcp_remote.py tests/test_mcp_stdio_proxy.py tests/test_legacy_token_shared_helper.py -q`
   Traps: same `git add` rule. Confirm the new tests fail with the src change
   reverted (`git stash` is forbidden: copy the file aside instead).

3. `tests/postgres/test_api_key_expiry_pg.py` (new), marks
   `pytest.mark.postgres, pytest.mark.slow`, uses the `test_postgres_url`
   fixture from `tests/postgres/conftest.py`. Own schema
   (`apikey_expiry_pg`, `search_path` via `connect_args`), `NullPool`,
   `Base.metadata.create_all`, seed user/org/member. Mint through the router
   with `expires_in_days: 1` and `scopes: ["api"]` → 201; then
   `_try_authenticate_api_key(raw_key)` (monkeypatch `get_db`) returns the
   principal, which writes `last_used_at`; then set `expires_at` to the past
   and `_lookup_by_digest` raises "expired". Drop the schema on teardown.
   (Pattern: AIFactory `tests/postgres/test_api_key_expiry_pg.py`.)
   → verify: `apps/backend/.venv/bin/python -m pytest tests/postgres/test_api_key_expiry_pg.py -q`
   skips locally without `TEST_POSTGRES_URL`; runs in CI `postgres-acceptance`.
   If a local Postgres is not available, rely on CI.
   Traps: same `git add` rule; no cluster or gitops access.

## Tests

All verify commands above pass; `ruff check` and `ruff format --check` on
touched files; `scripts/ratchet_lint.py` if present. Pre-commit runs the rest.

## Rollback

Revert the PR. No migration, no data change.

## Deviations

- Step 1: `MCP_READ_SCOPE` is imported absolutely (`server.mcp_remote.auth`) so
  no new TID252 finding is added; `_role_level` is a sibling relative import.
- Step 2: `_role_level` is a module-level import in `mcp_remote/auth.py` (no
  cycle). The lazy `cap_by_owner_role` import in `auth.py` carries
  `# noqa: PLC0415`, the repo's convention, so the lint ratchet passes. The
  `MCPAuthError` docstring raise-site count was already stale (6, said 5); it
  now says 7.
