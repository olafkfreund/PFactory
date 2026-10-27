---
status: draft
issue: 937
author: olafkfreund
---

# Intent: MCP acw_ key lookup enforces expiry and owner status

Closes #937. Refs olafkfreund/Factory#312. Mirrors AIFactory #1913.

## Problem

`mcp_remote/auth.py::_lookup_by_digest` is used by the stdio MCP proxy (always mounted) and by remote MCP (opt-in). It has two problems:
- it reads `row.is_active`, a column `ApiKey` does not have, so every real key raises AttributeError and the caller gets a 500;
- it never checks `expires_at`.

PFactory's REST path (`auth.py::_try_authenticate_api_key`) already checks both expiry and the owner's `User.is_active`.

## Proposed outcome

The MCP lookup applies the REST path's rules:
- an expired key fails;
- a key whose owner is missing or deactivated (SCIM soft-delete) fails;
- revoked keys still fail, because revoke hard-deletes the row.

Behaviour change: acw_ keys start authenticating on PF MCP paths for the first time.

## Affected users and systems

PFactory web-server `mcp_remote/auth.py`, which serves stdio and remote MCP clients.

## Constraints

No schema change. No role cap here: PF has no project RBAC, so a viewer's JWT already reaches the same routes (filed separately; the cap lands with the Factory#312 RBAC slice).

## Open questions

None.
