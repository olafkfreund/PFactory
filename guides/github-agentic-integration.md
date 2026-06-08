# GitHub Agentic Integration

> Epic [#87](https://github.com/olafkfreund/PFactory/issues/87). Adapts the
> Factory blueprint (Factory#24) and the AIFactory reference implementation
> (AIFactory#456) to PFactory's planning/decomposition domain.

PFactory gains a four-part, **opt-in, additive** integration with GitHub's
agentic surface. Nothing changes when the new config is absent.

| # | Component | What it does | Issue |
|---|-----------|--------------|-------|
| 1 | GitHub Models provider | Free OpenAI-compatible inference via `models.github.ai` | [#88](https://github.com/olafkfreund/PFactory/issues/88) |
| 2 | Copilot cloud-agent dispatch | `copilot:delegate` label → Copilot drafts a plan PR | [#88](https://github.com/olafkfreund/PFactory/issues/88) |
| 3 | Planning-context MCP server | `POST /mcp` exposes the plan to other agents | [#89](https://github.com/olafkfreund/PFactory/issues/89) |
| 4 | GitHub Actions workflows | Issue→task, Copilot-PR→review gates | [#90](https://github.com/olafkfreund/PFactory/issues/90) |

---

## Component 1 — GitHub Models provider

A first-class `github-models` provider that routes through the existing
`openai-compatible` backend with GitHub defaults pre-injected. **No new provider
class.**

- Model strings: `github-models/<publisher>/<model>` — e.g.
  `github-models/openai/gpt-4.1`, `github-models/openai/o4-mini`.
- Auth: `GITHUB_TOKEN` or `GH_TOKEN` (the same token `gh` already uses).
- Endpoint: `https://models.github.ai/inference` (injected automatically).
- Inference: `phase_config.infer_provider_from_model()` recognises the
  `github-models/` prefix *ahead of* the `gpt-`/`codex` check, so
  `github-models/openai/gpt-4.1` is **not** mistaken for Codex.
- Catalog: `GET /api/github/models` lists the live catalog (via `gh`).

Friendly shorthands (`gpt-4.1`, `o4-mini`, `llama-3.3-70b`, `deepseek-r1`) live
in `phase_config.GITHUB_MODELS_SHORTHANDS` — deliberately **separate** from
`MODEL_ID_MAP` (which routes to Claude). The canonical, unambiguous form is the
full `github-models/...` string.

> Note: bare `github` is **not** a provider alias — that would shadow the
> `gh`/GitHub API integration used across the codebase.

In CI, `permissions: models: read` grants `GITHUB_TOKEN` the scope to call the
Models API at zero cost (added to `ci.yml`).

## Component 2 — Copilot cloud-agent dispatch

When a PFactory planning issue carries the `copilot:delegate` label, PFactory
assigns it to GitHub's Copilot cloud agent (`copilot-swe-agent[bot]`). The agent
produces a **plan-draft PR** (requirements, decomposition, Task Contract v2
skeleton); PFactory's governance gates then review it (Component 4).

- Enable: `PFACTORY_COPILOT_DISPATCH_ENABLED=1` (default off).
- Auth: reuses the `gh` token — no new PAT (needs repo + issues +
  pull_requests scopes).
- Endpoints: `GET /api/github/copilot/config`, `POST /api/github/copilot/dispatch`
  (409 when disabled — caller falls back to the normal flow, never silent),
  `GET /api/github/copilot/pr` (find the Copilot-opened PR).

Implementation: `apps/web-server/server/services/copilot_dispatch_service.py`.

## Component 3 — Planning-context MCP server

A JSON-RPC 2.0 MCP server at **`POST /mcp`** that exposes PFactory's planning
outputs so the Copilot cloud agent (and AIFactory / TFactory) can retrieve
planning context at runtime — the bridge for RFC-0002 Task Contract v2.

| Tool | Returns |
|------|---------|
| `pfactory_get_epic` | epic decomposition |
| `pfactory_get_requirements` | normalised requirements + acceptance criteria |
| `pfactory_get_decomposition` | child-issue dependency graph |
| `pfactory_get_task_contract` | signed Task Contract v2 (built on demand) |
| `pfactory_get_review_status` | governance review-gate status |

- Lookup: by emitted GitHub epic **issue number** or PFactory **session_id**.
- Auth: `Authorization: Bearer ${PFACTORY_MCP_SECRET}` (open when unset — dev only).
- Data source: the in-memory `plan.service.SERVICE` store.

Configure in the GitHub repo (**Settings → Copilot → MCP servers**):

```json
{
  "mcpServers": {
    "pfactory": {
      "type": "http",
      "url": "${COPILOT_MCP_PFACTORY_URL}/mcp",
      "headers": { "Authorization": "Bearer ${COPILOT_MCP_PFACTORY_TOKEN}" },
      "tools": [
        "pfactory_get_epic",
        "pfactory_get_requirements",
        "pfactory_get_decomposition",
        "pfactory_get_task_contract",
        "pfactory_get_review_status"
      ]
    }
  }
}
```

Agents-secrets to set: `COPILOT_MCP_PFACTORY_URL` (your public URL) and
`COPILOT_MCP_PFACTORY_TOKEN` (= `PFACTORY_MCP_SECRET`).

For local dev, expose the endpoint with a tunnel:

```bash
cloudflared tunnel --url http://localhost:3114
# → set COPILOT_MCP_PFACTORY_URL to the printed https URL
```

Implementation: `apps/web-server/server/routes/mcp_rpc.py`. Catalogued as the
`pfactory-planning-mcp` API entity in `catalog-info.yaml`.

## Component 4 — GitHub Actions workflows

| Workflow | Trigger | Action |
|----------|---------|--------|
| `pfactory-task.yml` | `issues.labeled` = `pfactory:run` | Ingest the issue as a plan (`/api/plan/sessions/ingest-text`), or comment "planning queued" |
| `copilot-plan-review.yml` | PR opened by `copilot-swe-agent[bot]` | Run plan-review gates (`/api/github/prs/{pr}/plan-review`), or inline Copilot CLI review |

Both honour `permissions: models: read`. Repo secrets: `PFACTORY_URL` (your
instance) and `PFACTORY_TOKEN` (web-server API token). When unset, each workflow
falls back gracefully so the label/PR is never silently dropped.

The `plan-review` endpoint posts its gate-summary comment only when
`PFACTORY_PLAN_REVIEW_COMMENT=1` (the no-automatic-pushes policy); otherwise it
returns the rendered body for the workflow to post.

### Copilot Automations (configure in GitHub UI — no YAML)

- **Auto-label planning issues:** add `pfactory:run` to issues tagged
  `area:decompose` or matching planning keywords.
- **Weekly planning sweep:** scan open milestones, open PFactory tasks for
  unplanned items.

---

## Configuration reference

```bash
# Component 1 — GitHub Models (reuses gh token)
GITHUB_TOKEN=ghp_...

# Component 2 — Copilot dispatch (opt-in)
PFACTORY_COPILOT_DISPATCH_ENABLED=0

# Component 3 — MCP server
PFACTORY_MCP_SECRET=<random-32-char-hex>

# Component 4 — plan-review PR comment (default DRY-RUN)
PFACTORY_PLAN_REVIEW_COMMENT=0

# GitHub Actions secrets (Settings → Secrets → Actions)
# PFACTORY_URL   = https://your-pfactory-instance
# PFACTORY_TOKEN = web-server API token

# GitHub Copilot Agents secrets (Settings → Copilot → Agents secrets)
# COPILOT_MCP_PFACTORY_URL   = https://your-pfactory-instance
# COPILOT_MCP_PFACTORY_TOKEN = value of PFACTORY_MCP_SECRET
```
