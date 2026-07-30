# API & MCP

PFactory exposes three programmable surfaces:

1. **REST API** — the FastAPI portal backend (`apps/web-server`).
2. **WebSocket** — live progress, logs, events and terminal streams.
3. **MCP control plane** — a stdio JSON-RPC server for Claude Code and other
   agents (`apps/backend/mcp_server`).

## REST API

- **Base URL:** `http://localhost:3114` (default `APP_PORT`); hosted at
  `https://pfactory.freundcloud.com`.
- **Auth:** `Authorization: Bearer <token>` where the token is read from
  `~/.pfactory/.token` (auto-generated on first start, or pinned via
  `APP_API_TOKEN`).
- **Live contract (source of truth):** `GET /openapi.json`. With `APP_DEBUG=true`,
  Swagger UI is at `/docs` and ReDoc at `/redoc`. The
  [curated OpenAPI spec](https://github.com/olafkfreund/PFactory/blob/main/openapi.yaml)
  registered in the Backstage catalog is a hand-maintained subset.

!!! note "Response envelope"
    The frontend `api-client` wraps successful responses as
    `{ success: true, data: <payload> }` and errors as
    `{ success: false, error: "..." }`. Backend route handlers return the raw
    payload; the envelope is a client-side convention.

### The product surface — plan pipeline

The plan session lifecycle is the heart of PFactory (router prefix
`/api/plan/sessions`):

| Method & path | Purpose |
|---|---|
| `GET /api/plan/sessions` | List plan sessions (tenant-filtered when multi-tenant mode is on) |
| `POST /api/plan/sessions/ingest-text` | Create a session from raw text / markdown |
| `POST /api/plan/sessions/ingest` | Create a session from an uploaded PDF / DOCX / MD |
| `POST /api/plan/sessions/from-issue` | Create a session from an upstream GitHub issue (RFC-0011 hard tier) |
| `GET /api/plan/sessions/{id}` | Get a session and its state |
| `POST /api/plan/sessions/{id}/process` | Run enrich → detect → decompose → synthesize → feasibility → review → annotate |
| `POST /api/plan/sessions/{id}/approve` | **Human approval gate** — unlocks emission |
| `POST /api/plan/sessions/{id}/reject` | Reject; blocks emission |
| `POST /api/plan/sessions/{id}/emit` | Emit governed GitHub epics + child issues (and optionally trigger AIFactory) |

#### `POST /api/plan/sessions/from-issue`

The door AIFactory's RFC-0011 intake poller routes `factory:hard` issues
through (AIFactory#874): the issue body becomes the plan text, and the origin
issue number becomes the session's correlation key — recorded at intake, so
the chain back to the filed issue exists even for sessions that never reach
emit. The payload mirrors the poller's contract exactly:

```json
{
  "repo": "owner/name",
  "provider": "github",
  "issue_number": 123,
  "title": "optional issue title",
  "body": "the issue body — this IS the plan text",
  "labels": ["factory:hard"],
  "autonomy_tier": "hard",
  "change_mode": "accepted-but-unused"
}
```

`repo` and `issue_number` are required. `labels` and `change_mode` are
accepted (the poller sends them) but not acted on — the tier is already
classified in `autonomy_tier`, and `change_mode` is an AIFactory build-time
concern.

**The issue body must carry acceptance criteria**, in one of two forms:

1. A `#`-heading containing `acceptance criteria`, `acceptance`, or
   `requirements` (any level, e.g. `## Acceptance Criteria`), followed by
   bullet or numbered items.
2. Inline `AC#N: ...` lines anywhere in the body.

A body without either returns an actionable
`400: no acceptance criteria found — add an '## Acceptance Criteria' section
with bullets, or 'AC#N: ...' lines.` A bare title is deliberately not used as
a fallback: it carries nothing to verify against.

### Multi-tenancy

Off by default. When `PFACTORY_MULTI_TENANT` is truthy (`1` / `true` / `yes` /
`on`), the tenant is resolved per request from the `X-Tenant-Id` header the
ingress/oauth2-proxy stamps from the Keycloak `tenant` claim (falling back to
`default` when absent). With the flag on (#308):

- plan sessions are stamped with the tenant at intake, and
  `GET /api/plan/sessions` lists only the caller's tenant;
- the durable `job_states` store carries a `tenant_id` column (Alembic
  migration `20260717_a7d2e4b8c1f3`);
- a non-default tenant is written into emitted Task Contracts as
  `provenance.tenant_id`, so AIFactory can keep the PARR chain tenant-scoped.
  Default-tenant contracts are byte-identical to single-tenant output.

With the flag off, everything resolves to the single `default` tenant and
behaviour is unchanged.

Supporting plan routers:

- `/api/plan/*` (`plan-intake`) — stateless ingestion helpers.
- `/api/plan/meta/*` (`plan-meta`) — `registry`, `templates`, `categories`,
  `providers`, `adapters`.

### Portal & platform routers

Mounted in `apps/web-server/server/main.py`:

| Prefix | Tag | Purpose |
|---|---|---|
| `/api/auth`, `/api/auth/oidc` | Auth | Token + OIDC login |
| `/api/orgs` | Organizations / Audit | Org management + audit trail |
| `/api/keys` | API Keys | API-key management |
| `/api/git-credentials`, `/api/test-credentials` | Credentials | Portal-managed clone auth & test-target logins |
| `/api/provider-runtimes`, `/api/llm-endpoints` | Providers | LLM provider runtimes & endpoints |
| `/api/cloud` | Cloud | Read-only cloud posture assessment (AWS / Azure / GCP) |
| `/api/visual-inspections` | Visual Inspection | Visual-regression baselines |
| `/api/audit`, `/api/users` | Audit / GDPR | Audit export + GDPR erasure |
| `/api/notifications` | Notifications | Portal notifications |
| `/api/projects` | Projects / Auto-Fix / MCP | Project registration & discovery |
| `/api/tasks` | Tasks / Task Execution | Task lifecycle |
| `/api/settings` | Settings / CLI Accounts | App settings + CLI accounts |
| `/api/files` | Files | Read/list files by absolute path |
| `/api/terminals` | Terminals | PTY sessions |
| `/api/github` | GitHub | Issues & PR automation; GitHub-agentic surface — `models` catalog, `copilot/{config,dispatch,pr}`, `prs/{pr}/plan-review` (epic #87) |
| `/api/capabilities` | Capabilities | Runtime capability probe |
| `/api/git`, `/api/ollama`, `/api/claude-code`, `/api/mcp`, `/api/updates` | Git / tooling | Git ops + tooling status |
| `/api/memory` | Memory | Graphiti session context |
| `/api/logs`, `/api/skills` | Logs / Skills | Logs + skills registry |

## WebSocket

Real-time channels (tag `WebSocket`):

| Channel | Purpose |
|---|---|
| logs | Streaming agent / build logs |
| progress | Pipeline phase progress events |
| events | General portal events |
| terminal | Interactive PTY I/O |

For remote deployments set `VITE_WS_BASE_URL` to the server's WebSocket URL.

## MCP control plane

PFactory ships a **stdio MCP server** so agents (Claude Code) can drive the
pipeline as tools.

- **Server name:** `pfactory` (registered via
  `create_sdk_mcp_server(name="pfactory", ...)` in
  `apps/backend/agents/tools_pkg/registry.py`).
- **Launch:** `scripts/start-pfactory-mcp.sh` (referenced by the repo-root
  `.mcp.json`), which runs `apps/backend/mcp_server/pfactory_server.py` over
  stdio using `apps/backend/.venv`.
- **Env:** `PFACTORY_PROJECT_DIR`, `PFACTORY_SPEC_DIR` / `--spec-dir`,
  `PFACTORY_API_URL` (default `http://localhost:3114`),
  `PFACTORY_API_TOKEN_FILE` (default `~/.pfactory/.token`).

Tools are assembled per session from category factories
(`create_subtask_tools`, `create_progress_tools`, `create_memory_tools`,
`create_qa_tools`) and namespaced `mcp__pfactory__*` (e.g.
`mcp__pfactory__update_subtask_status`). The same `pfactory` name is used both
in-process (Claude Agent SDK sessions) and by the standalone server — a single
source of truth post-rebrand.

## HTTP MCP — planning context (GitHub agentic)

Distinct from the stdio control plane above, PFactory also exposes its **planning
outputs** as an HTTP MCP server so the GitHub Copilot cloud agent (and
AIFactory / TFactory) can retrieve the plan *at runtime* — the bridge for
[Task Contract v2](task-contract.md). Part of the GitHub Agentic Integration
(epic [#87](https://github.com/olafkfreund/PFactory/issues/87)).

- **Transport:** JSON-RPC 2.0 over `POST /mcp` (a POST-only Streamable-HTTP
  subset). Handles `initialize`, `tools/list`, `tools/call`, `ping`,
  notifications. Route: `apps/web-server/server/routes/mcp_rpc.py`.
- **Auth:** `Authorization: Bearer ${PFACTORY_MCP_SECRET}` (constant-time; the
  endpoint is open when the secret is unset — dev only).
- **Lookup:** by emitted GitHub epic issue number, or by PFactory `session_id`.
  Data source: the in-memory `plan.service.SERVICE` store.

| Tool | Returns |
|---|---|
| `pfactory_get_epic` | Epic decomposition (title, body, summary, cost/effort) |
| `pfactory_get_requirements` | Normalised requirements + acceptance criteria |
| `pfactory_get_decomposition` | Child-issue dependency graph |
| `pfactory_get_task_contract` | Signed RFC-0002 Task Contract v2 (built on demand) |
| `pfactory_get_review_status` | Governance review-gate status |

Register it in the repo's **Settings → Copilot → MCP servers** with the JSON
snippet in [`guides/github-agentic-integration.md`](https://github.com/olafkfreund/PFactory/blob/main/guides/github-agentic-integration.md);
catalogued as the `pfactory-planning-mcp` API entity in `catalog-info.yaml`.

The same epic adds the **GitHub Models provider**
(`github-models/<publisher>/<model>` → free `models.github.ai` inference authed by
`GITHUB_TOKEN`), **Copilot cloud-agent dispatch** (`copilot:delegate` →
`copilot-swe-agent[bot]`), and two **GitHub Actions** workflows (issue → plan,
Copilot PR → review gates). All four components are opt-in and additive.

## CLI

Headless planning without the portal:

```bash
python apps/backend/cli/main.py --spec <id>
```
