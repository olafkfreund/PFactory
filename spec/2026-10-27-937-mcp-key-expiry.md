---
status: approved
issue: 937
intent: intent/2026-10-27-937-mcp-key-expiry.md
---

# Spec: MCP acw_ key lookup enforces expiry and owner status

## Design

In `apps/web-server/server/mcp_remote/auth.py::_lookup_by_digest`, replace `if not row.is_active` with two checks:
1. If the row has an owner (`row.user_id`), load `session.get(User, row.user_id)`. A missing user, or `User.is_active` False, raises `MCPAuthError("API key owner is deactivated")`.
2. If `expires_at` is `<= now`, raise `MCPAuthError("API key has expired")`. A naive datetime is treated as UTC.

These are the same rules as `auth.py::_try_authenticate_api_key` and AIFactory #1913.

Before dropping the check I verified on origin/dev:
- revoke hard-deletes the row;
- `api_keys` never had `is_active` or a soft-delete column;
- `User.is_active` is the only other disabled state.

## Alternatives rejected

- Add an `is_active` column to `ApiKey`. That is a schema change, and revoke already deletes the row.
- Reuse `_try_authenticate_api_key`. It requires the REST `api` scope and returns a principal dict, which is a different contract.

## Risks

Keys start working on MCP paths. Their scopes still gate every proxy.

## Verification

New `tests/test_mcp_key_lookup.py` against a real SQLite DB covers revoked, expired, unexpired, deactivated-owner and active-owner keys. Every case except revoked fails on dev. Also run `tests/test_mcp_remote.py`, `tests/test_api_key_auth.py` and `tests/*mcp_stdio*`, plus `scripts/ratchet_lint.py`.
