---
name: pfactory-watch
description: Poll a running PFactory task to completion, then pick up its triage report and verify the generated tests cover the acceptance criteria. One check per invocation — drive it with /loop for a hands-off round-trip. Reads task_status over MCP and the triage report file directly (no backend dependency on the bugged report_get).
when_to_use: After /handover-to-pfactory returns a task_id and you want to wait for the autonomous pipeline (Planner → Gen-Functional → Executor → Evaluator → Triager) to finish, then automatically review the result. Common triggers — "watch the pfactory task", "/pfactory-watch <task_id>", "is the pfactory task done yet", "pick up the pfactory report when ready", "/loop 30s /pfactory-watch <task_id>".
allowed-tools:
  - mcp__pfactory__task_status
  - mcp__pfactory__task_list
  - mcp__pfactory__task_rerun
  - Read
  - Bash
---

# /pfactory-watch

Poll one PFactory task, and when it finishes, pick it up and **verify** that
the autonomously-generated tests actually cover the acceptance criteria you
handed off. This skill does **one** status check per invocation — to make it a
hands-off loop, run it under `/loop`:

```
/loop 30s /pfactory-watch <task_id>
```

`/loop` re-fires it on the interval; the skill tells the loop to **stop** as
soon as the task reaches a terminal state.

## Arguments

- `task_id` (required) — the id returned by `/handover-to-pfactory`
  (`task_id == spec_id`). If omitted, call `mcp__pfactory__task_list` and ask
  the user which task to watch.

## Procedure (one pass)

### Step 1 — poll status

Call `mcp__pfactory__task_status(task_id=<task_id>)`. Read two fields:

- `status` — the lifecycle state
- `phase` — fine-grained sub-state (for a friendly progress line)

Also note `project_id` and `spec_id` from the response — you need them to
locate the report in Step 3.

### Step 2 — classify the state

| Bucket | `status` values | Action |
|---|---|---|
| ✅ **Done** | `triaged`, `triaged_empty` | go to Step 3 (pick up + verify), then **STOP the loop** |
| ❌ **Failed** | anything ending in `_failed`, or `stuck` | report the failure + remediation (Step 4), then **STOP the loop** |
| ⏳ **Running** | everything else (`pending`, `planning`, `planned`, `generating`, `generated`, `evaluating`, `evaluated`, `triaging`, and the transient `*_empty` intermediates) | print a one-line progress update (`status` / `phase`) and **let the loop re-fire** — do NOT read the report yet |

> Important: `planned_empty` / `generated_empty` / `evaluated_empty` are
> **not** terminal — they auto-advance to the next stage. Only `triaged*` and
> `*_failed` / `stuck` stop the loop.

To stop the loop, finish your turn without scheduling another iteration (state
plainly that the task is terminal so `/loop` does not re-fire).

### Step 3 — pick up the report + VERIFY

The Triager writes the report to the workspace, **not** via `report_get`
(which is currently wired to the wrong path). Read it directly:

```
WS="${PFACTORY_WORKSPACE_ROOT:-$HOME/.pfactory/workspaces}"
SPEC_DIR="$WS/<project_id>/specs/<spec_id>"
# Read $SPEC_DIR/findings/triage_report.md  (and findings/triage_report.json)
```

Use the `Read` tool on `$SPEC_DIR/findings/triage_report.md`. Then **verify**:

1. List the acceptance criteria you handed off (from the task's goals / the
   `context/aifactory_spec.md` the pipeline planned against).
2. For each accepted/flagged test in the triage report, note which `AC#N` its
   rationale covers.
3. **Flag any acceptance criterion with no covering accepted test** — that's a
   coverage gap, not a pass.
4. Summarise for the user: ✅ covered ACs, ⚠️ flagged tests (and why), ❌ ACs
   with no test, plus the verdict counts (committed / flagged / rejected).
5. If `status == triaged_empty` (no tests survived triage), say so explicitly —
   the pipeline ran but nothing passed the 5-signal verdict; recommend a
   `task_rerun` or revisiting the acceptance criteria.

### Step 4 — on failure / stuck

- `*_failed` — report which stage failed (the `status` names it:
  `planner_failed` / `gen_functional_failed` / `evaluator_failed` /
  `triager_failed`) and surface `status.json` error fields + the relevant
  `logs/<agent>.log` line if present.
- `stuck` — the Planner hit `replan_count >= 2`; the acceptance criteria are
  likely ambiguous or untestable. Surface the latest `context/replan_request.json`
  reason and ask the user to refine the criteria.
- Offer `mcp__pfactory__task_rerun(task_id, lane=<lane>)` as the retry path.

## Notes

- **Pure read-only round-trip** — `task_status` is cheap + safe to poll; the
  report is read from disk. No backend changes, no writes (except an optional
  user-approved `task_rerun`).
- **Cadence:** `30s` is a good default; the pipeline typically runs minutes.
  For long browser-lane runs, `/loop 60s` is fine.
- **Pairs with `/handover-to-pfactory`** — that skill hands off and returns the
  `task_id`; this one watches it home and verifies the result.
