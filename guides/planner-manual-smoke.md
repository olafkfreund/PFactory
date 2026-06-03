# Planner — Manual Real-LLM Smoke

This guide walks through firing the PFactory Planner against a **real
AIFactory spec** with a **real Anthropic API key**. The unit + integration
tests under `tests/test_planner*.py` mock the SDK so they cost nothing
and run in under 2 seconds; this smoke is the last-mile validation
that the planner produces useful plans against a non-fixture spec.

> **Cost:** one full Planner session uses ~5-15K input tokens (system
> prompt + spec + diff) and ~1-4K output tokens (the emitted plan).
> At Claude Sonnet 4 prices, that's roughly **$0.01-0.05 per smoke
> run**. The token budget is bounded by the 30-subtask cap.

## Prerequisites

- A real `ANTHROPIC_API_KEY` in your environment (`.env` at repo root works).
- An AIFactory project that's produced at least one finished spec — i.e.
  there's a directory at `~/.aifactory/workspaces/{project_id}/specs/{spec_id}/`
  with at minimum `spec.md` inside.
- The PFactory backend installed:

  ```bash
  nix develop                # or whatever your dev environment is
  bootstrap-venv             # installs claude-agent-sdk + friends
  ```

- The MCP server running (so `/handover-to-pfactory` can dispatch):

  ```bash
  scripts/start-pfactory-mcp.sh
  ```

## Path A: end-to-end via `/handover-to-pfactory`

This is the production flow — Claude Code inside your AIFactory project
calls the MCP, the MCP creates a PFactory workspace, auto-fires the
planner.

1. Install the companion skill into your AIFactory repo:

   ```bash
   mkdir -p ~/Source/GitHub/AIFactory/.claude/skills/handover-to-pfactory
   cp companion-skills/aifactory-handover-to-pfactory/SKILL.md \
      ~/Source/GitHub/AIFactory/.claude/skills/handover-to-pfactory/SKILL.md
   ```

2. Register PFactory's MCP server in AIFactory's `.mcp.json` (see the
   skill's body for the exact JSON snippet).

3. In a Claude Code session opened **inside AIFactory**, type:

   ```
   /handover-to-pfactory
   ```

4. Watch the MCP call dispatch. The task is auto-fire'd (default
   `PFACTORY_AUTO_PLAN=1`). Poll status:

   ```bash
   # in another shell
   watch -n 2 'jq . ~/.pfactory/workspaces/<project_id>/specs/<spec_id>/status.json'
   ```

   You should see: `pending → planning → planned` (or `planned_empty` /
   `planner_failed` in degraded cases).

5. Inspect the emitted plan:

   ```bash
   jq . ~/.pfactory/workspaces/<project_id>/specs/<spec_id>/test_plan.json
   ```

## Path B: direct CLI (bypasses the MCP)

For when you want to drive the planner against a pre-existing workspace
without going through Claude Code:

```bash
# 1. Ensure a PFactory workspace exists with the snapshot already populated.
#    (task_create_and_run does this; here we assume it's there.)
WS=~/.pfactory/workspaces/<project_id>/specs/<spec_id>

# 2. Run the planner directly.
nix develop --command bash -c "
  cd $PWD
  PYTHONPATH=apps/backend apps/backend/.venv/bin/python -c '
import asyncio
from pathlib import Path
from agents.planner import run_planner

ok = asyncio.run(run_planner(
    spec_dir=Path(\"'$WS'\"),
    project_dir=Path(\"/path/to/your/aifactory/project\"),
    mode=\"initial\",
    verbose=True,
))
print(f\"ok={ok}\")
'"
```

## Expected outcomes

| Outcome | Meaning |
|---|---|
| `ok=True`, `status=planned`, `subtask_count` 5-15 | Healthy plan, expected for most specs |
| `ok=True`, `status=planned`, `subtask_count = 30` | Truncated; the spec was very large. Check `planner_warnings`. |
| `ok=True`, `status=planned_empty` | Agent didn't find anything to test — usually a spec / diff that doesn't change executable code. |
| `ok=False`, `status=planner_failed`, `phase=planner_session_error` | API key missing, quota exhausted, or network failure. Check `planner_error` in status.json. |
| `ok=False`, `phase=planner_invalid_*_after_retry` | Two consecutive sessions emitted invalid output. Inspect `logs/planner.log` to see what the agent produced. |

## What to verify by hand

Open `test_plan.json` and check each emitted subtask:

- [ ] `target` references a real file + symbol that the diff actually touched
- [ ] `rationale` cites the acceptance criterion it covers (not vague — should be quotable)
- [ ] `files_to_create` is a sensible path under `tests/...`
- [ ] `verification.command` ends with `pytest <some path>` (the path
      should align with `files_to_create`)
- [ ] `lane` is `"functional"` on every subtask (MVP — other lanes are
      gated by the dispatcher and shouldn't appear yet)
- [ ] Phase names map to acceptance criteria, not generic "phase 1 / phase 2 / …"

If anything's off, file an issue against the Planner — examples of bad
output help calibrate future prompt tweaks.

## Replan smoke

To exercise the replan path manually:

1. After an initial `planned` run, edit `test_plan.json` to set one
   subtask's `id` you want to "reject" — e.g. `s0`.
2. Write a replan request:

   ```bash
   cat > $WS/context/replan_request.json <<EOF
   {
     "subtask_id": "s0",
     "reason": "hallucinated import: nonexistent_module",
     "failed_target": "app/foo.py::nope"
   }
   EOF
   ```

3. Run the planner with `mode="replan"`:

   ```python
   await run_planner(spec_dir=Path(WS), project_dir=Path(PROJECT), mode="replan")
   ```

4. The new `test_plan.json` should have:
   - All existing phases preserved
   - One new phase named `replan-1` with a single corrected subtask
   - The original subtask's `replan_count` bumped from 0 → 1
   - `status.json` now has `last_replan_for: "s0"`, `last_replan_count: 1`,
     `last_replan_stuck: false`

5. Repeat once more on the same subtask. After the second replan,
   `replan_count` should be 2 and `last_replan_stuck` should be `true`.

## Cleanup

The workspace lives under `~/.pfactory/workspaces/`. Each task has its
own subdir; delete what you don't need:

```bash
rm -rf ~/.pfactory/workspaces/<project_id>/specs/<spec_id>
```

## Where this output flows next

A successful planner run lands `test_plan.json` in the workspace; the
**Gen-Functional agent (Task 6, issue #7)** is what consumes it next.
At the time this guide was written, Task 6 hasn't shipped yet, so the
plan sits idle. Once Task 6 lands, the pipeline runs to completion
without further manual steps.
