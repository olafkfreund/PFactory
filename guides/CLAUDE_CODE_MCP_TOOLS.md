# MCP Control-Plane Tools

> Drive PFactory tasks from a Claude Code session in the PFactory repo — no portal switch required.

When you open the PFactory repo in Claude Code, the project-scoped `.mcp.json` registers our stdio MCP server. The server exposes two toolsets:

- **Spec-internal tools** (already shipped) — record discoveries, update subtask/QA status, query build progress. These act on the *active spec*.
- **Task-control tools** (this guide, Epic #50 M1) — list / inspect / start / stop / approve tasks across the whole install. These talk to the running web-server's REST API.

Both sets live under the `mcp__pfactory__*` namespace. Same `.mcp.json` entry, single mental model.

## Prerequisites

1. **Web-server running** on the host. Default: `http://localhost:3102`. Start with:

   ```bash
   cd apps/web-server && python -m server.main
   ```

2. **API token at `~/.pfactory/.token`**. The web-server auto-generates this on first start and the token gets printed to stdout. The MCP server reads it at every tool call (so rotating the token doesn't need a restart).

3. **Override paths via env** (optional — defaults are sensible):

   | Env var | Default | Purpose |
   |---|---|---|
   | `PFACTORY_API_URL` | `http://localhost:3102` | Where to reach the web-server |
   | `PFACTORY_API_TOKEN_FILE` | `~/.pfactory/.token` | Path to the bearer token |

   These are wired into `.mcp.json` so a Claude Code session in the repo picks them up automatically.

## Trust model

Tools have **full admin access** via the legacy bearer token. Anyone with the token can drive every task on the install. This matches the current pilot scope. Per-user MCP tokens land in the v1.1 RBAC Epic (#41 SAML+SCIM); until then, treat `~/.pfactory/.token` as a root password.

Every write tool writes an `AuditLog` row server-side with `action=mcp.task.<verb>` so all MCP-initiated state changes are traceable.

## The 8 M1 tools

### Read tools

#### `task_list`

```
What can it do? List tasks across all projects.
Args: status (optional), project_id (optional), limit (default 50)
Returns: lean entries with id, title, status, project_id, created_at
```

**Example prompt:**

> "Show me the running tasks across all projects"

Claude Code will call `task_list({status: "running"})` and report back.

#### `task_running`

```
What can it do? Just-running shortcut — same shape as task_list({status: "running"}) but always-current via GET /api/tasks/running.
Args: none
Returns: id, title, project_id, phase, started_at
```

#### `task_get`

```
What can it do? Full task detail.
Args: task_id (required)
Returns: full task payload with requirements_json / test_plan_json
         truncated at 2000 chars so the LLM context doesn't bloat.
```

**Example prompt:**

> "What's the state of task abc123? Show me the implementation plan."

The plan field comes back truncated to keep the response sensibly sized. Hit the REST API directly if you want the whole thing.

#### `task_status`

```
What can it do? Just the execution-state object — cheaper than task_get.
Args: task_id (required)
Returns: { phase, current_subtask, overall_progress, model_in_use }
```

Use this for polling.

#### `task_get_logs`

```
What can it do? Last N log lines.
Args: task_id (required), tail (default 100, capped at 500)
Returns: the log lines.
```

### Write tools

Each writes an `AuditLog` row with `action=mcp.task.<verb>`.

#### `task_start`

```
What can it do? Start an agent for a task.
Args: task_id (required)
Returns: { started: true, task_id, details }
Audit: action=mcp.task.start
```

**Example prompt:**

> "Start task abc123"

#### `task_stop`

```
What can it do? Terminate the running agent subprocess. Resumable via task_start.
Args: task_id (required)
Returns: { stopped: true, task_id, details }
Audit: action=mcp.task.stop
```

#### `task_approve_plan`

```
What can it do? Approve the implementation plan at the human-review checkpoint so the agent resumes.
Args: task_id (required)
Returns: { approved: true, task_id, details }
Audit: action=mcp.task.approve_plan
```

**Example prompt:**

> "Approve the plan for task abc123 and let it continue"

## Error handling — what each failure mode looks like

The MCP tools never raise; failures land as a content block with `isError: true`. Examples:

| Situation | What you see |
|---|---|
| Web-server isn't running | `Error: PFactory web-server not reachable at http://localhost:3102 — start it with: python -m server.main` |
| Token file missing | `Error: PFactory API token not found at ~/.pfactory/.token — regenerate via the web UI or run: python -m server.main` |
| Token rejected | `Error: PFactory token at ~/.pfactory/.token rejected — regenerate via the web UI` |
| Task id not found | `Error: Resource not found at GET /api/tasks/xyz (HTTP 404)` |
| Server error (5xx) | `Error: PFactory web-server returned HTTP 503: <body, truncated to 500 chars>` |

All are single-line — no stack traces dumped into the chat.

## Walkthrough — full task lifecycle from Claude Code

A complete demo flow without leaving the chat:

```
You: What tasks are currently running?
Claude: [calls task_running] → "No tasks running. There are 3 paused tasks: ..."

You: Start task spec-042-auth-validation
Claude: [calls task_start({task_id: "spec-042-auth-validation"})] → "Started."

You: How's it going?
Claude: [calls task_status({task_id: "spec-042-auth-validation"})] → "Phase: planning, 15% complete..."

You: Show me the implementation plan once it's ready
Claude: [polls task_status, then calls task_get when phase is human_review]
        → "Here's the plan. 8 subtasks, focus on the JWT middleware..."

You: Looks right. Approve it.
Claude: [calls task_approve_plan({task_id: "spec-042-auth-validation"})] → "Approved."

You: If it gets stuck, stop it.
Claude: [later, after detecting stuck phase] [calls task_stop] → "Stopped."
```

## M2 tools — PR + recovery + project ops

M2 (#52) adds 7 more tools. Destructive writes (`create_and_run`, `recover`, `create_pr`, `merge_pr`) **require `confirm=true`** — they refuse on first call with a structured `requires_confirmation` response so an autonomous LLM doesn't kick off paid agent runs or merge production PRs unprompted.

### `task_create_and_run` (destructive)

```
Create a new task from a description and start it immediately.
Args: project_id, title, description, model (optional), confirm
Returns (with confirm=true): { created_and_started: true, details: {task_id, ...} }
Audit: action=mcp.task.create_and_run
```

**Example flow:**

> User: "Create a task in project X to add OAuth login"
> Claude: [calls task_create_and_run without confirm]
> Claude: "I can create this task — it'll kick off a paid agent run. Should I proceed? (Set confirm=true to actually run it.)"
> User: "Yes, do it"
> Claude: [calls task_create_and_run with confirm=true]

### `task_recover` (destructive)

```
Recover a stuck task — restarts the agent from its last checkpoint.
Args: task_id, auto_restart (default false), confirm
Audit: action=mcp.task.recover
```

With `auto_restart=false` the task is left paused after recovery so a human can inspect first.

### `task_create_pr` (destructive)

```
Create a GitHub PR from the task's worktree branch.
Args: task_id, title (optional), body (optional), confirm
Returns (with confirm=true): { created: true, details: {pr_url, pr_number} }
Audit: action=mcp.task.create_pr
```

Title/body default to the spec title + summary.

### `task_merge_pr` (destructive)

```
Merge the task's open PR into the project's default branch.
Args: task_id, merge_method (merge|squash|rebase, default merge), confirm
Returns (with confirm=true): { merged: true, details: {sha} }
Audit: action=mcp.task.merge_pr
```

### `task_get_diff`

```
Get the worktree diff for a task — what the agent has written so far.
Args: task_id, max_lines (default 1000)
Returns: { lines, truncated, diff }
```

Truncates at `max_lines` so big diffs don't blow up the LLM context. The response includes a `truncated: true` flag + a `...[truncated after N lines]` marker on the last line.

### `project_list`

```
List all projects registered with this PFactory install.
Args: none
Returns: { count, projects: [{id, name, path, git_provider}] }
```

### `agent_status`

```
Single-call answer to "what's this agent doing right now?"
Args: task_id
Returns: { phase, model, current_subtask_id, current_subtask_title, overall_progress }
```

Combines `task_status` (phase + progress) with the task's `phaseModels` config so the response is one coherent payload. Cheaper than calling `task_status` + `task_get` separately.

## Walkthrough — full task lifecycle from create to merge (M1+M2)

```
You: List my projects.
Claude: [project_list] → "p1 (pfactory-demo, github), p2 (...)"

You: In pfactory-demo, create a task to fix the login button alignment.
Claude: [task_create_and_run with project_id=p1, title="Fix login button alignment", description=..., confirm=false]
        → "I'd run a paid agent on pfactory-demo with this description: ...
           Should I proceed? Set confirm=true."

You: Yes, run it.
Claude: [task_create_and_run with confirm=true] → "Started, task_id=spec-099-fix-login..."

You: How's it going?
Claude: [agent_status] → "Phase: planning, model sonnet-4-6, 12% complete, current subtask: 'Inspect button CSS'"

You: Show me the plan once it's ready.
Claude: [polls agent_status; when phase=human_review] [task_get] → "8 subtasks, mostly CSS..."

You: Approve.
Claude: [task_approve_plan] → "Approved."

You: When coder finishes, show me the diff.
Claude: [polls; when phase=qa or coding_done] [task_get_diff] → "47 lines changed in 3 files..."

You: Looks good. Open the PR.
Claude: [task_create_pr with confirm=false]
        → "I'd open a PR titled 'Fix login button alignment'. Confirm to actually create it."

You: confirm.
Claude: [task_create_pr with confirm=true] → "Created PR #142: https://github.com/..."

You: Merge with squash.
Claude: [task_merge_pr with merge_method=squash, confirm=false]
        → "I'd squash-merge PR #142 into main. Confirm?"

You: confirm.
Claude: [task_merge_pr with merge_method=squash, confirm=true] → "Merged, sha=abc123."
```

Three pauses (`create_and_run`, `create_pr`, `merge_pr`) — the confirm-gates make destructive ops explicit two-turn interactions.

## Coming after that (#83 — remote MCP control plane)

Same control-plane surface, different transport: an HTTP+SSE MCP server inside the web-server at `/api/mcp` so non-Claude clients (Cursor, Continue.dev) can drive PFactory too. Opt-in via `PFACTORY_MCP_REMOTE_ENABLED=true`.
