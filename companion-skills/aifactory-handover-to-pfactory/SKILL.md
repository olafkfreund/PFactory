---
name: handover-to-pfactory
description: From an AIFactory project, hand a finished spec off to PFactory (sister project) for autonomous test generation. Records the task with PFactory's MCP server; once PFactory Tasks 5-8 land, this also drives the planner→generator→executor→evaluator→triager pipeline.
when_to_use: When the user has finished an AIFactory feature on a branch and wants PFactory to generate aligned pytest tests + a coverage/security report. Common triggers — "hand this off to pfactory", "/handover-to-pfactory", "have pfactory test this spec", "generate tests for the current PR".
allowed-tools:
  - mcp__pfactory__project_list
  - mcp__pfactory__project_create
  - mcp__pfactory__task_create_and_run
  - mcp__pfactory__task_status
  - mcp__pfactory__task_list
  - mcp__pfactory__report_get
  - mcp__pfactory__task_rerun
  - Bash
---

# /handover-to-pfactory (AIFactory companion)

This skill lives **inside an AIFactory project** and is the user-facing
trigger for handing a finished AIFactory spec off to PFactory.

It is the mirror image of `PFactory/.claude/skills/handover-to-pfactory/`.
Both files share the same procedure; the only practical difference is
which repo the slash command is typed from. This one installs into
`AIFactory/.claude/skills/handover-to-pfactory/SKILL.md`.

## Installation

From the **AIFactory repo root**:

```bash
mkdir -p .claude/skills/handover-to-pfactory
cp /path/to/PFactory/companion-skills/aifactory-handover-to-pfactory/SKILL.md \
   .claude/skills/handover-to-pfactory/SKILL.md
```

Then register PFactory's MCP server in AIFactory's `.mcp.json` (or your
user-level Claude Code MCP config) so the `mcp__pfactory__*` tools are
reachable. Typical project-scoped form:

```json
{
  "mcpServers": {
    "pfactory": {
      "type": "stdio",
      "command": "bash",
      "args": [
        "/absolute/path/to/PFactory/scripts/start-pfactory-mcp.sh"
      ],
      "env": {
        "PFACTORY_PROJECT_DIR": "${CLAUDE_PROJECT_DIR:-.}",
        "PFACTORY_API_URL": "http://localhost:3102",
        "PFACTORY_WORKSPACE_ROOT": "~/.pfactory"
      }
    }
  }
}
```

Once both files are in place, `/handover-to-pfactory` is available
inside Claude Code sessions opened in the AIFactory project.

## When to use

Trigger when the user signals "ship the tests" or "have pfactory cover
this":

- explicit `/handover-to-pfactory`
- "hand this over to pfactory"
- "generate tests for spec X"
- "have pfactory test this PR"

If the user is mid-feature and the branch isn't ready, push back rather
than handing over a half-built thing.

## Procedure

### 1. Gather the four required arguments

The PFactory MCP tool needs `project_id`, `spec_id`, `branch`,
`base_ref`. Infer from conversation + git state; only ask for what's
missing.

| Argument | How to determine |
|---|---|
| `project_id` | The AIFactory project ID. Visible in `~/.aifactory/projects.json`, in the portal URL, or in the active spec's path. |
| `spec_id` | The AIFactory spec ID — the directory name under `~/.aifactory/workspaces/<project_id>/specs/`. Usually obvious from recent chat or `ls ~/.aifactory/workspaces/<project_id>/specs/`. |
| `branch` | `git rev-parse --abbrev-ref HEAD`. |
| `base_ref` | The PR base. Default `main`; use `git merge-base HEAD origin/main` if needed. |

### 1b. Ask what to focus on + whether to enable a visual inspection (#170)

Before previewing, ask the user (skip whichever is already clear):

1. **What should PFactory focus on?** — the task intent / acceptance focus.
2. **Enable a visual inspection?** — for UI-heavy features (or a SaaS target like
   ServiceNow), PFactory can record a Playwright **browser** run, capture per-step
   verification + error screenshots, and package a human **visual-inspection
   report** + correction plan into `automated-test/<datetime>/` (surfaced in the
   portal's *Visual Reports*). If yes, gather the **visual target** name (a
   `visual: true` target in `.pfactory.yml`) and the **flow** to inspect.

Pass these as the optional `visual_inspection` argument to `task_create_and_run`:
`{ "enabled": true, "target": "<name>", "flow": "<what to inspect>" }`. Omit it for
a normal code-test task — the default path is unchanged.

### 2. Confirm the project is registered with PFactory

Call `mcp__pfactory__project_list`. If the AIFactory project isn't
present, register it:

```
mcp__pfactory__project_create(
  id=<aifactory project_id>,
  name=<human readable name>,
  root_path=<absolute path to local checkout>
)
```

### 3. Preview, then commit

First `task_create_and_run` with `confirm=false` for the preview. Show
the workspace path to the user. On confirmation, call again with
`confirm=true`. Capture and report `task_id`, `spec_dir`, `portal_url`.

### 4. Report and (optionally) poll

A one-line summary back to the user. If they want progress, call
`task_status` once after a beat. Once Tasks 5-8 land, the Triager will
have written `report.md`/`report.json` — fetch with `report_get`.

## Failure modes

- **Unknown project** → walk the user through `project_create` first.
- **Spec already handed over** → offer `task_rerun` instead (MVP only
  supports the `functional` lane).
- **PFactory MCP server not reachable** → the user needs to start it
  via `scripts/start-pfactory-mcp.sh` in the PFactory repo, and confirm
  the AIFactory `.mcp.json` points at the right absolute path.

## When the tests find problems — hand back for a fix

If PFactory's run finishes with failing tests / rejects, you can hand the
problems back to AIFactory for a fix with **`/handback-to-aifactory`** (install
its companion from `PFactory/companion-skills/aifactory-handback-from-pfactory/`).
It applies the correction PFactory prepared to the original spec via AIFactory's
QA Fixer, closing the loop:

```
/handover-to-pfactory → test → (failures) → /handback-to-aifactory
   → AIFactory QA Fixer → re-run PFactory to verify
```

## Status at MVP

Workspace creation + status tracking work. The pipeline (planner →
generators → executor → evaluator → triager) is scheduled for PFactory
Tasks 5-8; until then `task_create_and_run` records the task with
`status=pending` and you can introspect via `task_status` /
`task_list`.
