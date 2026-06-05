# Architecture

## Planning pipeline

```
ingest ──▶ enrich ──▶ detect ──▶ decompose ──▶ review gates ──▶ approve ──▶ emit
            │                                      │                          │
   cloud / Backstage / wikis        architecture · security ·        GitHub epics +
   Terraform · K8s / cloud          best-practices · feasibility      child issues
                                    (hybrid policy + LLM, cited)      → AIFactory
```

## Components

```
apps/
├── backend/
│   ├── agents/            # Planner / Gen-Functional / Evaluator / Triager
│   │   └── tools_pkg/     # MCP tool registry (plan_*, task_control)
│   ├── plan/              # ingest → enrich → decompose → review → emit
│   │   ├── enrich/        # cloud + Backstage + knowledge adapters
│   │   ├── review/        # gate lenses & verdict logic
│   │   └── emit/          # GitHub + AIFactory handoff
│   ├── core/              # Claude SDK client, auth, security
│   ├── mcp_server/        # stdio MCP server (pfactory_server.py)
│   ├── providers/         # multi-LLM provider factory
│   ├── templates_pkg/     # Backstage-compatible template registry
│   └── integrations/      # Graphiti memory, credential broker
├── web-server/            # FastAPI REST + WebSocket (:3114)
└── frontend-web/          # React 19 + Vite portal (:3115)
```

## Runtime

- **CLI:** `python apps/backend/cli/main.py --spec <id>` (headless planning).
- **Portal:** FastAPI backend `:3114` + Vite frontend `:3115`.
- **MCP control plane:** `scripts/start-pfactory-mcp.sh` → stdio JSON-RPC for Claude Code.

## Data & state

Workspace at `~/.pfactory/workspaces/<project>/specs/<spec>/` holds `status.json`, the
normalized plan, review findings (with citations), feasibility estimates, and logs.
Key models: `NormalizedPlan` (id, title, criteria, enrichment, content hash) and
`EpicPlan` (decomposed epics + children).

## Integration

- **Outbound → AIFactory:** emits GitHub epics + child issues (`requirements.json` with
  `metadata.githubIssueNumber`); can trigger AIFactory's create-and-run API.
  See `apps/backend/plan/emit/aifactory_handoff.py`.
- **Inbound ← TFactory (handback):** receives correction requests for re-planning.
- **Completion events:** emits the [RFC-0001](https://factory.freundcloud.com/rfc/correlation-key/)
  envelope on terminal status (usage instrumentation pending).
- **Read-only cloud introspection** during enrich/review via cloud adapters + a
  vault-backed credential broker (credentials ephemeral, redacted from logs).
