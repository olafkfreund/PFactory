---
status: approved
issue: 937
spec: spec/2026-10-27-937-mcp-key-expiry.md
---

# Plan: MCP acw_ key lookup enforces expiry and owner status

Decisions:
- Replace the dead `row.is_active` check with an owner-active check, and add an expiry check.
- Error messages are "API key owner is deactivated" and "API key has expired".
- No role cap and no schema change.

## Steps

1. `apps/web-server/server/mcp_remote/auth.py` around line 126: apply the two checks above. Import `User` alongside `ApiKey` and `datetime, UTC`, and update the `authenticate` docstring bullets. Add `tests/test_mcp_key_lookup.py`.
   → verify: `apps/backend/.venv/bin/python -m pytest tests/test_mcp_key_lookup.py tests/test_mcp_remote.py tests/test_api_key_auth.py -q`, then the ratchet command from `.github/workflows/cq-ratchet.yml`.
   Traps: TID252 counts edited relative-import lines as new, so make the touched imports absolute (`server.database...`). Never `git add -A`.

## Tests

As above, plus the stdio MCP tests.

## Rollback

Revert the PR.
