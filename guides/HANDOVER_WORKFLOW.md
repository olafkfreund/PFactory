# Developer → PFactory Handover Workflow

> "I'm 20 minutes into a Claude Code session. The refactor is bigger than I thought. I type `/handover`, Claude summarises what we've been discussing, hands it to PFactory's autonomous pipeline, and gives me a URL. I go to lunch. I come back to a draft PR + a QA report waiting for my review."

This guide covers the **`/handover` slash command** — the 1-keystroke way to move a task from a live Claude Code session into PFactory's autonomous build pipeline.

## Why this exists

You have two complementary tools:

| | Claude Code (interactive) | PFactory (autonomous) |
|---|---|---|
| Best for | Debugging, exploration, design decisions, anything needing your judgement turn-by-turn | Bulk refactors, dep upgrades, test backfilling, doc generation, well-specified features |
| Cost | Expensive cognitive load + Claude tokens for every turn | Cheaper per-task (planner → coder → QA runs without human supervision until the review gate) |
| Latency | Synchronous — you wait for each turn | Async — you walk away, come back to a PR |

`/handover` is the bridge between them. You start interactive, realise the task is mature enough to run autonomously, and move it across without leaving the chat.

## Prerequisites

1. **You're in a Claude Code session in a repo with the PFactory `.mcp.json`**.
   This file registers the stdio MCP server. The PFactory monorepo already has one at the root.

2. **The PFactory web-server is running**.

   ```bash
   cd apps/web-server && python -m server.main
   ```

   It listens on `http://localhost:3102` by default. Override with `PFACTORY_API_URL`.

3. **The API token exists at `~/.pfactory/.token`** (the web-server creates this on first start and prints the value once). Override path with `PFACTORY_API_TOKEN_FILE`.

That's it. The MCP server (`apps/backend/mcp_server/pfactory_server.py`) is started by Claude Code on-demand when you open the repo, with the right env vars set in `.mcp.json`.

## Usage

### Most common — let Claude infer the task

```
You: ... 20 minutes of back-and-forth designing a JWT refresh-token flow ...
You: /handover
Claude: ✅ Handed off to PFactory.

         • Task: spec-099-jwt-refresh-tokens
         • Title: Add JWT refresh tokens to auth middleware
         • Track at: http://localhost:3102/tasks/p1:spec-099-jwt-refresh-tokens

         PFactory's planner runs first — when it hits the plan-review gate
         the portal will show the implementation plan for your approval.
```

Claude reads the recent conversation, summarises it, picks a title, and calls `mcp__pfactory__task_create_and_run` under the hood.

### Explicit override

When the conversation is thin or you want to be precise:

```
/handover refactor the GitHub provider in apps/backend/runners/github/providers/github_provider.py to support GitLab MR auto-rebase. Acceptance: existing tests still pass + new test for the rebase path.
```

The text after `/handover` becomes the description verbatim.

### When Claude pushes back

If the conversation context is too thin and you didn't provide an override, Claude will ask a clarifying question instead of guessing:

```
You: /handover
Claude: I'd want to make sure I capture the right task — we've only had 2
        short exchanges so far. Could you give me a one-line description of
        what you want PFactory to build? Or paste the relevant requirements?
```

This is intentional — handing the wrong task to an autonomous agent burns a paid run on the wrong thing.

## What happens after handover

1. **Status: `created` → `planning`** — PFactory's planner agent gathers context, writes `spec.md`, produces `test_plan.json`.

2. **Status: `planning` → `human_review`** — the plan is ready for your approval. The portal shows it; you can also poll from Claude Code with `mcp__pfactory__task_status` or open the URL directly.

3. **You approve (or reject) the plan** — via the portal, OR from Claude Code with `mcp__pfactory__task_approve_plan` / the M2 `mcp__pfactory__task_create_and_run`-style flow.

4. **Status: `human_review` → `coding` → `qa` → `done`** — the coder agent runs, writes commits, the QA agent reviews. Each phase is autonomous; you only intervene at human gates.

5. **A draft PR appears** when the build completes — open it from the portal or run `mcp__pfactory__task_create_pr` if you want to drive that from Claude Code too.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/handover` not recognised | Restart Claude Code so it picks up `.claude/skills/handover/SKILL.md` |
| `Error: PFactory web-server not reachable...` | Start the web-server: `cd apps/web-server && python -m server.main` |
| `Error: PFactory API token not found...` | The web-server prints the token on first start; regenerate via the web UI if you lost it |
| `Error: ... rejected — regenerate via the web UI` | The token at `~/.pfactory/.token` doesn't match what the DB has — regenerate |
| Claude picks the wrong PFactory project | Manually run `mcp__pfactory__project_list`, then re-run `/handover` with the right project context |
| Plan-review gate notifications never arrive | Not implemented yet — V1 requires you to poll the portal. V2 (notifications) is on the roadmap |

## Comparison to alternatives

**Use `/handover` when**: the task is well-defined enough to step away from, and you want async + autonomous execution.

**Don't use `/handover` when**: you're debugging (interactive Claude is faster), the task is exploratory (you're still figuring out what to build), or you'd benefit from staying in the loop turn-by-turn.

**You can also just call the underlying MCP tools directly** from Claude Code:

- `mcp__pfactory__task_create_and_run` — the primitive `/handover` wraps
- `mcp__pfactory__task_status` — poll a running task
- `mcp__pfactory__task_approve_plan` — approve from the chat
- `mcp__pfactory__task_get_logs` — read agent output
- (15 stdio tools total — see [CLAUDE_CODE_MCP_TOOLS.md](./CLAUDE_CODE_MCP_TOOLS.md))

`/handover` is the *workflow-level shortcut*. Power-users can wire their own slash commands or call tools directly.

## What's NOT in V1 (and what's coming)

This V1 deliberately scopes to **Claude Code users in the PFactory repo**. The MCP control-plane Epic (#50) shipped tonight built the foundation; this V1 is the polish on top.

**V2 follow-ups (planned)**:

- **Remote MCP `pfactory.create_and_run` parity** — so Cursor / Continue.dev users can hand off from their IDE. The Remote MCP server (`/api/mcp-remote/sse`) currently lacks the create+run tool; the V1.1 PR deliberately deferred it.
- **`pfactory handover` CLI binary** — for IDEs without MCP support (JetBrains, plain terminal).
- **Notification loop** — when a handed-off task hits the plan-review gate, fire a Slack DM, email, or portal badge to whoever ran `/handover`. Closes the async loop competitors (Cursor / Devin / Copilot) all ship.
- **Per-user MCP tokens with `mcp:handover` scope** — current handovers use the legacy admin bearer; audit logs attribute the action to the admin user instead of the originating developer. v1.1 RBAC (#41 SAML+SCIM) fixes this.
- **Conversation-transcript snapshot** — capture the full Claude Code conversation up to `/handover` and seed it into the spec as `conversation_context.md`. Higher fidelity than the LLM-summarised description.

## What other tools call this

For context, the major incumbents all ship variants of this pattern:

| Tool | Trigger | What it does |
|---|---|---|
| **Cursor 3 Background Agents** (Apr 2026) | `/background` or chat handoff | Snapshots local agent state, uploads to a cloud sandbox, continues async — up to 8 parallel |
| **Devin** (Cognition Labs) | `/handoff` slash command | Sends to managed cloud Devin, integrates with Slack + GitHub |
| **GitHub Copilot Coding Agent** (GA Sep 2025) | "Assign to Copilot" from an issue / chat / `gh copilot delegate` CLI | Bot spins up GitHub Actions, opens a draft PR, commits to it live |

PFactory's V1 differentiates by being **self-hosted, customer-controlled, audit-traceable, and reusing the same agent pipeline interactive users already trust** — there's no new "background-mode" variant, the same planner → coder → QA → human-review runs on every task whether it was started via the portal or `/handover`.
