---
status: draft
issue: 861
intent: intent/2026-10-15-861-derive-lane-progress.md
---

# Spec: derive lane_progress from the task status on read

## Design

New `apps/backend/agents/lane_progress.py` with
`derive_lane_progress(spec_dir, status) -> dict[str, str] | None`.

Lanes it sets: the lanes named by `test_plan.json` subtasks (`lane` field);
when there is no readable plan, the keys of the stored `lane_progress` (a
task that failed before planning). Lanes outside that set keep the stored
value. Returns None when there is nothing to set (caller keeps the stored
value).

Each set lane takes a value from the existing status vocabulary, so the
portal's `statusColor` (`PFactoryTaskList.tsx:58`) colours it with no
frontend change:

| task `status` | lane value | colour |
|---|---|---|
| `evaluating` | `evaluating` | in flight |
| `evaluated`, `triaging`, `triaged`, `triaged_empty`, `triager_failed` | `evaluated` | green |
| `planner_failed`, `gen_functional_failed`, `evaluator_failed`, `stalled` | same as status | red |
| `planned_empty`, `generated_empty`, `evaluated_empty` | same as status | yellow |
| anything else (pending, planning, planned, generating, generated, replan_needed) | stored value | — |

`triager_failed` maps to `evaluated`: the lanes ran; only triage failed, and
the task status already shows that failure.

Both readers apply it on read, without writing `status.json`:

- MCP `task_status` (`task_control.py:473`) sets `status["lane_progress"]`
  from it before returning.
- Portal `get_task` (`pfactory_tasks.py:229`) does the same on
  `status_doc`. The web-server already imports `agents.*` (`routes/cloud.py:36`).

Derived on read means tasks already on disk are fixed with no backfill, and
it cannot go stale.

## Alternatives rejected

- Port TFactory's per-lane evidence logic (verdict lanes, run artifacts,
  staleness floor): PFactory verdicts carry no `lane` and PFactory has no
  verdict-to-subtask resolver, so it needs much more code for a finer signal
  nobody asked for here. A per-lane error state can follow if wanted.
- Write `lane_progress` in `write_status_patch` on every status change: does
  not fix tasks already on disk, and the rerun path writes `status.json` raw.
- New lane values (`executed`, `error`) like TFactory: needs a frontend
  colour change; the existing vocabulary already colours correctly.

## Risks

- A lane in the plan with no generated test still shows `evaluated` after
  evaluation. Coarse, not wrong about the pipeline; recorded.
- Unreadable `test_plan.json` falls back to the stored lanes; never raises.

## Verification

`tests/test_lane_progress.py`:
1. task `triaged` with a plan naming `unit` and `api`: MCP `task_status`
   returns `unit`/`api` = `evaluated`, others stored `pending`.
2. task `planner_failed`, no plan: every lane `planner_failed`.
3. task `planning`: unchanged (all pending).
Plus a portal `get_task` test in `tests/test_pfactory_routes_tasks.py` for
the `triaged` case. Both fail on `origin/dev` and pass after.
