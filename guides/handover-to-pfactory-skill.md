# The `/handover-to-pfactory` companion skill

> Reference doc for sub-task 12.4. The actual skill definition lives in
> [`.claude/skills/handover-to-pfactory/SKILL.md`](../.claude/skills/handover-to-pfactory/SKILL.md);
> this guide explains what it does, where it lives on the AIFactory side,
> and how operators wire it up.

The `/handover-to-pfactory` skill is the operator-facing on-ramp to the
PFactory pipeline. A Claude Code session in an AIFactory repo invokes
this skill (via `/handover-to-pfactory` or natural language like "hand
this off to pfactory") and the four-agent pipeline takes over.

## Where it lives

The skill is checked into THIS repo at
`.claude/skills/handover-to-pfactory/SKILL.md`. There are two ways
AIFactory sessions can discover it:

### Option 1 — symlink from AIFactory (recommended)

In the AIFactory repo:

```bash
mkdir -p .claude/skills
ln -s /path/to/PFactory/.claude/skills/handover-to-pfactory \
      .claude/skills/handover-to-pfactory
```

Pros: the AIFactory side always sees the canonical PFactory version;
updating the skill in PFactory propagates automatically.

### Option 2 — copy from AIFactory

```bash
cp -r /path/to/PFactory/.claude/skills/handover-to-pfactory \
      AIFactory/.claude/skills/handover-to-pfactory
```

Pros: independent of PFactory checkout location. Cons: drifts if the
canonical version updates.

## Required MCP server

The skill's `allowed-tools` list references `mcp__pfactory__*` tools.
These come from the PFactory MCP server at
`apps/backend/mcp_server/pfactory_server.py`. The AIFactory repo's
`.mcp.json` (or the user's `~/.claude.json`) must register it:

```json
{
  "mcpServers": {
    "pfactory": {
      "command": "python",
      "args": [
        "-m", "apps.backend.mcp_server.pfactory_server"
      ],
      "cwd": "/path/to/PFactory",
      "env": {
        "PYTHONPATH": "/path/to/PFactory/apps/backend"
      }
    }
  }
}
```

The PFactory repo ships its own `.mcp.json` at the root with the
canonical entry; copy that block into the AIFactory repo's `.mcp.json`
or your user-level one.

## What the skill does (one-line summary)

1. Resolves the current AIFactory project on disk and looks it up (or
   creates it) via `mcp__pfactory__project_create`.
2. Calls `mcp__pfactory__task_create_and_run` with the project_id,
   branch, base_ref, and root_path. The backend's
   `task_control.task_create_and_run` then:
   - Calls the snapshotter (Task 3) to freeze the AIFactory spec dir,
     plan.json, and `git diff base_ref..branch` into
     `~/.pfactory/workspaces/<proj>/specs/<spec>/context/`.
   - Writes `status.json` with `status=pending`.
   - With `PFACTORY_AUTO_PLAN=1` (default), schedules the Planner.
3. The pipeline auto-advances Planner → Gen-Functional → Evaluator →
   Triager. The user polls via `mcp__pfactory__task_status` or watches
   the portal at `:3114`.

## What it does NOT do

- It does **not** post a PR comment by default (per CLAUDE.md
  "no automatic pushes" — `PFACTORY_TRIAGER_PR_COMMENT=1` to opt in).
- It does **not** commit tests to the AIFactory branch by default
  (`PFACTORY_TRIAGER_GIT_WRITE=1` to opt in).
- It does **not** require any Claude API key on the AIFactory side —
  the keys live in PFactory's environment because PFactory's agents are
  the ones calling out.

## Verifying the skill in AIFactory

After symlinking or copying, in an AIFactory Claude Code session:

```bash
# 1. Confirm the skill is discoverable
ls .claude/skills/handover-to-pfactory/SKILL.md

# 2. Confirm the MCP server is reachable
# In the session, type "/" — handover-to-pfactory should appear
```

Then invoke it: `/handover-to-pfactory` (or "hand this off to pfactory").
The skill will respond with the workspace path and a status URL.

## Related

- [`.claude/skills/handover-to-pfactory/SKILL.md`](../.claude/skills/handover-to-pfactory/SKILL.md) — the canonical skill file
- [`apps/backend/mcp_server/pfactory_server.py`](../apps/backend/mcp_server/pfactory_server.py) — MCP server exposing `mcp__pfactory__*` tools
- [`apps/backend/agents/tools_pkg/tools/task_control.py`](../apps/backend/agents/tools_pkg/tools/task_control.py) — `task_create_and_run` implementation + Planner auto-fire scheduler
- [`guides/HANDOVER_WORKFLOW.md`](HANDOVER_WORKFLOW.md) — operator-facing flow doc (AIFactory user → PFactory autonomous build)
- [`guides/CLAUDE_CODE_MCP_TOOLS.md`](CLAUDE_CODE_MCP_TOOLS.md) — full MCP tool reference
- [`guides/e2e-smoke.md`](e2e-smoke.md) — 9-scenario manual smoke runner
