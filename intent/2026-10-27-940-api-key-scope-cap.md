---
status: draft
issue: 940
author: olafkfreund
---

# Intent: cap acw_ key scopes by org role at mint and at auth, and store naive UTC times

Closes #940. Refs olafkfreund/Factory#312. Mirrors AIFactory #1911 and #1914.

## Problem

1. `POST /api/keys` (`routes/api_keys.py::create_api_key`) checks org
   membership only. A viewer can mint `api`, `mcp:write`, `task:write`,
   `task:merge`, `plan:write` or `project:write`. A key can also mint a key.
2. Neither the REST key path (`auth.py::_try_authenticate_api_key`) nor the
   MCP lookup (`mcp_remote/auth.py::_lookup_by_digest`, which the stdio-MCP
   proxy also uses) checks that the owner is still a member of `key.org_id`,
   or caps scopes by their current role. A removed or demoted member keeps
   the stored scopes.
3. `create_api_key` stores an aware `expires_at`, and the REST path stores an
   aware `last_used_at`, in naive `DateTime` columns. asyncpg rejects both.

## Proposed outcome

- Keys cannot mint keys (403). Below `member`, a caller may request only
  `mcp:read`; anything else is 403. Member and above are unchanged.
- On REST, remote MCP and stdio MCP, a key's effective scopes are capped per
  call by the owner's current role in `key.org_id`: no membership means
  rejected, below member means `mcp:read` only, and an empty result is
  rejected.
- Minting with an expiry and using a key over REST work on Postgres.

## Affected users and systems

PFactory web-server: `routes/api_keys.py`, `auth.py`, `mcp_remote/auth.py`.
Prod PF `api_keys` is empty, so no live key changes behaviour.

## Constraints

- No new roles or scope constants. Use `ROLE_LEVELS` / `_role_level` and the
  existing `mcp:read`.
- No project/role RBAC on task routes (that is Factory#312). The cap matters
  fully once that lands; until then a key grants nothing beyond its owner's JWT.
- No gitops, cluster or credential changes.

## Open questions

None. Out of scope: route-level binding of a key to its own org on
org-scoped routes (AIF #1914 did this in `require_org_role`); it grants
nothing beyond the owner's JWT today and belongs with Factory#312.
