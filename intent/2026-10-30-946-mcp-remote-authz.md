---
status: approved
issue: 946
author: olafkfreund
---

# Intent: remote MCP tools authorize the calling key, not the service token

Closes #946. Refs olafkfreund/Factory#312. Mirrors AIFactory #1910 (PR #1917).

## Problem

`apps/web-server/server/mcp_remote/tools.py::_call_internal` proxies every tool
call to REST with `Bearer settings.API_TOKEN`. REST sees the service principal
and runs no org or project check. `dispatch_tool_call` only checks the key's
scope. So a key with `mcp:write` in org A can start, stop, approve, reject,
recover and merge tasks in any org, and the read tools (`list_projects`,
`list_tasks`, `get_task`, diff, QA report, console URL) leak across orgs.

The SSE session also runs every tool as the key that opened it, but
`POST /messages/` accepts any valid key, so a second key can drive a session it
does not own.

## Proposed outcome

- Each task or project tool authorizes the key's owner against the target
  project before it proxies: `viewer` for `mcp:read` tools, `member` for
  `mcp:write` tools. Always enforced, not behind `RBAC_ENFORCE_TASK_ROUTES`.
- `list_projects` returns only projects in the key's org, and only while the
  owner is still a member of it.
- Task ids without `:` and ids with path characters are refused.
- `POST /messages/` is refused (403) unless the posting key opened the session.
- Own-org calls work as before. Ownerless (service) keys are unchanged.

## Affected users and systems

PFactory web-server `mcp_remote/tools.py` and `mcp_remote/server.py`. Only
deployments with `PFACTORY_MCP_REMOTE_ENABLED` set. Off by default; no manifest on
factory-gitops main sets it.

## Constraints

No schema, gitops or deploy change. Reuse `project_authz.authorize_project_for_user`.
Do not forward the caller's key to REST (MCP-only keys lack `api`).
Projects without `org_id` deny non-service keys: acceptable while remote MCP is off.

## Open questions

None.
