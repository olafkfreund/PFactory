# Handing a plan to PFactory from an agent (Claude Code · Antigravity · Codex)

PFactory accepts a plan over **MCP** or plain **HTTP** so any agent/host can hand
work off, then poll it through review to approval. Handoff arrives on the `agent`
source channel. Nothing is emitted to GitHub/AIFactory until the AI gates pass
**and** a human approves.

## The flow

```
plan_ingest → plan_process → plan_status/plan_get → (human) plan_approve → emit
```

## MCP tools (`mcp__pfactory__plan_*`)

The standalone MCP server (`apps/backend/mcp_server/pfactory_server.py`) publishes:

| Tool | Purpose |
|------|---------|
| `plan_ingest` | Ingest inline `text` or a file `path` (+ optional `category`/`template`) → `session_id` |
| `plan_process` | Run enrich · feasibility · review → cited review + cost/effort |
| `plan_status` | `status` + board column + `gates_passed` |
| `plan_get` | Full review findings (with citations) + estimates |
| `plan_list` | All sessions |
| `plan_approve` | Record human approval (gates must have passed) |

### Claude Code

Add PFactory's MCP server to the project's `.mcp.json`, then the agent calls the
tools directly:

```jsonc
// .mcp.json
{ "mcpServers": { "pfactory": { "command": "python", "args": ["-m", "mcp_server.pfactory_server"] } } }
```

```
mcp__pfactory__plan_ingest(text: "<plan markdown>", category: "infrastructure")
mcp__pfactory__plan_process(session_id: "001-...")
mcp__pfactory__plan_get(session_id: "001-...")
```

### Antigravity / Codex / Copilot

These hosts can call the same tools if they support MCP, **or** use the HTTP API
below — identical semantics.

## HTTP API (host-agnostic)

The web-server mirrors every tool under `/api/plan/sessions`:

```bash
# 1. ingest
curl -s -X POST $PF/api/plan/sessions/ingest-text \
  -H 'content-type: application/json' \
  -d '{"text":"# Plan...\n## Acceptance Criteria\n- ...","title":"My plan","channel":"agent"}'
# → {"session_id":"001-my-plan", ...}

# 2. process
curl -s -X POST $PF/api/plan/sessions/001-my-plan/process

# 3. status / detail
curl -s $PF/api/plan/sessions/001-my-plan

# 4. human approval (after gates pass)
curl -s -X POST $PF/api/plan/sessions/001-my-plan/approve \
  -H 'content-type: application/json' -d '{"approver":"olaf"}'
```

`$PF` is the PFactory web-server base URL (e.g. `http://localhost:3198`).

## What you get back

`plan_process` / `plan_get` return the review with **cited** findings, the
**cost** (live cloud pricing) / **effort** / **access** (IAM-simulation)
feasibility estimates, and the `board_state` (`backlog → in_progress → ai_review
→ human_review → done`). A plan needing attention lands in `human_review`.

## Discovering categories / templates

`GET /api/plan/meta/categories` lists the selectable categories (product,
software, feature, hosting, infrastructure, testing, cicd, …) and their
templates, so an agent can set `category`/`template` on ingest.
