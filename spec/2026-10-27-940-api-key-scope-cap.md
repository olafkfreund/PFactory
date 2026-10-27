---
status: approved
issue: 940
intent: intent/2026-10-27-940-api-key-scope-cap.md
---

# Spec: cap acw_ key scopes by org role at mint and at auth, and store naive UTC times

## Design

1. **Mint** (`apps/web-server/server/routes/api_keys.py::create_api_key`), after
   the membership lookup:
   - If `request.state.user` is a dict with `api_key_id`: 403 "API keys cannot
     be created with an API key" (same guard as AIF, Factory#1960).
   - If `_role_level(membership.role) < _role_level("member")` and
     `set(body.scopes or ()) - {MCP_READ_SCOPE}`: 403 "Scopes beyond 'mcp:read'
     require the 'member' org role". Unknown roles (level -1) count as below
     member. `None` and `[]` stay allowed.
   - `expires_at` is stored as naive UTC (`.replace(tzinfo=None)`).
2. **Auth cap**: one helper `cap_by_owner_role(session, row, scopes)` in
   `apps/web-server/server/mcp_remote/auth.py`. It selects `OrgMember.role`
   where `org_id == row.org_id` and `user_id == row.user_id`. No row gives
   `frozenset()`; below member gives `scopes & {MCP_READ_SCOPE}`; otherwise
   `scopes` unchanged.
   - `_lookup_by_digest` calls it for keys with a `user_id` and raises
     `MCPAuthError("API key has no scopes for its owner's current role")` on an
     empty result. This covers remote MCP and the stdio-MCP proxy, which both
     call `authenticate`.
   - `auth.py::_try_authenticate_api_key` caps the parsed scopes with the same
     helper (lazy import, like the existing lazy imports there) before the
     `API_SCOPE` check. A viewer or non-member therefore loses REST access
     (`api` is not a read scope). It also stores `last_used_at` as naive UTC.
   The membership is looked up in `key.org_id` only, so membership in another
   org never counts.

## Alternatives rejected

- Switch the columns to a UTC type decorator (TFactory's `UTCDateTime`): a
  type change on more columns than needed; naive UTC at the two write sites is
  what AIF did and needs no migration.
- Route REST through `_lookup_by_digest`: changes the REST path's return-None
  contract and its tests for no gain; sharing the cap helper is enough.
- Bind keys to their org on org-scoped routes (`require_org_role`): grants
  nothing beyond the owner's JWT today; belongs with Factory#312.
- Revoke existing over-scoped keys: prod table is empty, and the auth-time cap
  neutralises any that exist.

## Risks

- A viewer automation that minted write keys now gets 403 at mint and loses
  write scopes at auth. Intended.
- Fake-session unit tests in `tests/test_api_key_auth.py` queue one result per
  `execute`; the new membership query adds one, so those fixtures need a role row.
- Import cycle: `mcp_remote/auth.py` importing `server.routes.organizations`
  at module level. If it cycles, import lazily inside the helper.

## Verification

- `tests/test_api_key_mint_scope_cap.py` (new, real SQLite DB through the
  router): viewer + `mcp:write` 403, viewer + `["mcp:read","task:merge"]` 403,
  viewer + `mcp:read` 201, member + `task:merge` 201, key principal 403.
  The viewer/write and key-principal tests fail on dev.
- `tests/test_mcp_key_lookup.py` (existing real-DB file): demoted-to-viewer key
  has only `mcp:read`; removed member is rejected; viewer key with only write
  scopes is rejected; membership in another org does not count. Fail on dev.
- REST real-DB test: a removed member's `api` key returns `None` from
  `_try_authenticate_api_key`. Fails on dev.
- `tests/postgres/test_api_key_expiry_pg.py` (marker `postgres`, runs in the
  `postgres-acceptance` CI job): mint with `expires_in_days` then REST auth
  (bumps `last_used_at`) succeed on Postgres. Fails on dev.
- ruff, targeted pytest, CI green.
