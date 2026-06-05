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
| `GET /api/plan/sessions` | List plan sessions |
| `POST /api/plan/sessions/ingest-text` | Create a session from raw text / markdown |
| `POST /api/plan/sessions/ingest` | Create a session from an uploaded PDF / DOCX / MD |
| `GET /api/plan/sessions/{id}` | Get a session and its state |
| `POST /api/plan/sessions/{id}/process` | Run enrich → detect → decompose → synthesize → feasibility → review → annotate |
| `POST /api/plan/sessions/{id}/approve` | **Human approval gate** — unlocks emission |
| `POST /api/plan/sessions/{id}/reject` | Reject; blocks emission |
| `POST /api/plan/sessions/{id}/emit` | Emit governed GitHub epics + child issues (and optionally trigger AIFactory) |

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
| `/api/github` | GitHub | Issues & PR automation |
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

## CLI

Headless planning without the portal:

```bash
python apps/backend/cli/main.py --spec <id>
```
