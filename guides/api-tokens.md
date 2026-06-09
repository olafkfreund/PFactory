# Personal API tokens (for MCP / programmatic access)

> Issue #93. Lets a logged-in (OIDC/SSO) user mint a GitHub-PAT-style token
> from **Settings → API Tokens** and use it as a `Bearer` token against the
> REST API, the MCP server, and the `/handover` skill — retiring the shared,
> deployment-level `APP_API_TOKEN` stopgap.

## Why

The deployed portal authenticates **users** via Keycloak/OIDC (no static
token). Programmatic clients — the MCP server and the `/handover` skill — need
a **Bearer token**. Before this, that meant the shared `APP_API_TOKEN` (a full-
API secret handed to every client) or a short-lived OIDC JWT. A **per-user
token** is attributable, individually revocable, and scope-limited.

## Mint a token

1. Open **Settings → API Tokens** in the portal.
2. Enter your **Organization ID** (UUID). A multi-org picker lands with Epic #35.
3. Click **Mint key**, give it a name (e.g. `laptop-ada`), pick **scopes**, and
   optionally an expiry (max 365 days).
4. **Copy the `acw_…` value immediately** — it is shown exactly once. The
   backend stores only a SHA-256 hash plus an 8-char preview; lose it and you
   must revoke + mint a new one.

### Scopes

| Scope          | Grants                                                            |
| -------------- | ---------------------------------------------------------------- |
| `api`          | **Full REST API** access — `GET/POST /api/*` (like a GitHub PAT) |
| `mcp:read`     | MCP read-only tools (list / status / logs)                       |
| `project:write`| Create new projects                                              |
| `task:write`   | Start / stop / recover / approve tasks                           |
| `task:merge`   | Create PRs + merge worktrees (high blast radius)                 |

A single token may carry several scopes. To use **one** token for both the REST
API *and* the MCP control plane, mint it with `api` **plus** the `mcp:*` scopes
you need.

> **Scope isolation:** the REST surface (`/api/*`) requires the `api` scope.
> A token minted only for MCP (e.g. `mcp:read`) will **not** unlock `/api/*` —
> that separation is deliberate. Grant `api` explicitly when you want REST
> access.

## Use the token

### REST API

```bash
curl -H "Authorization: Bearer acw_your_token" \
  https://your-deployment/api/projects        # → 200
```

The middleware resolves the token to its owner, so the request runs as **you**
(your user id, role, and org), and the token's `last_used_at` is updated.

### MCP server / `.mcp.json`

Point the MCP client's token file at your minted token instead of the shared
admin token:

- Set `$PFACTORY_MCP_KEY`, **or** drop the raw `acw_…` value in
  `~/.pfactory/.mcp-key`.
- The stdio MCP proxy and `/handover` skill pick it up automatically.

Mint that key with the scopes the handover needs — typically `api` (so the
skill can drive `/api/*`) plus `task:write` / `task:merge` for the MCP control
plane.

## Revoke

**Settings → API Tokens → trash icon.** Any laptop or shell using that key
loses access immediately (revocation deletes the row).

## Retiring `APP_API_TOKEN`

Once clients authenticate with per-user `acw_` tokens, the shared
deployment-level `APP_API_TOKEN` can be removed from the environment. It remains
supported as a legacy fallback for backward compatibility, but it is no longer
required for MCP / handover access.

## Implementation notes

- Mint / list / revoke: `apps/web-server/server/routes/api_keys.py`
  (`POST/GET/DELETE /api/keys`).
- REST acceptance: `TokenAuthMiddleware` in `apps/web-server/server/auth.py`
  (`_try_authenticate_api_key`, gated on the `api` scope; hashes match the
  mint route; expired or revoked keys are rejected).
- UI: `apps/frontend-web/src/components/settings/sections/McpApiKeysSettings.tsx`.
