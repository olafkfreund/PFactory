---
name: pfactory-fixloop
description: Close the AIFactory ↔ PFactory loop hands-off. After PFactory tests a feature and finds problems, this drives one bounded cycle — hand the correction back to AIFactory, wait for its QA Fixer, re-test in PFactory — and stops when the run passes, the same tests keep failing (no progress), or a correction-cycle cap is hit (→ stuck for a human). One cycle per invocation; drive it with /loop for a fully autonomous test → fix → re-test loop. Epic #182 P6.
when_to_use: When you want the test→fix→re-test loop to run itself after a /handover-to-pfactory, rather than hand-driving /handback-to-aifactory each round. Triggers — "/pfactory-fixloop <task_id>", "keep fixing until the tests pass", "auto-correct this until green", "/loop 60s /pfactory-fixloop <task_id>".
allowed-tools:
  - mcp__pfactory__task_status
  - mcp__pfactory__task_rerun
  - mcp__pfactory__report_get
  - mcp__aifactory__task_apply_correction
  - mcp__aifactory__task_status
  - Bash
  - Read
---

# /pfactory-fixloop

Drive **one bounded cycle** of the AIFactory ↔ PFactory correction loop, then
stop with a clear verdict. Under `/loop` it becomes the hands-off
test → fix → re-test loop the epic set out to build.

```
PFactory test → failures? → handback to AIFactory → QA Fixer → re-test
   └────────────────── bounded: cap, or no-progress → STUCK ──────────────┘
```

> **Why one cycle per invocation:** like `/pfactory-watch`, this skill does a
> single unit of work and returns, so `/loop` controls the cadence and you can
> interrupt at any point. The bound is enforced in code
> (`agents/handback/loop.py`) so the loop can never run away — it mirrors the
> Planner's `replan_count >= 2 → stuck` rule.

## The decision (computed, not guessed)

The next action is decided by `decide_loop(...)` over the latest run's failing
set — never improvise it:

| Latest run | Under cap? | Progress vs last cycle | Action |
|---|---|---|---|
| no failing tests | — | — | **passed** → stop, success |
| has failures | at/over cap | — | **stuck** → stop, hand to a human |
| has failures | under cap | same tests still fail | **stuck** → stop (no progress) |
| has failures | under cap | different/fewer failures | **retest** → hand back + re-run |

Cap defaults to **2** (override `PFACTORY_HANDBACK_MAX_CYCLES`).

## Procedure (one cycle)

### 1. Resolve the workspace + wait for terminal

Given `<task_id>`, find its PFactory spec dir
(`~/.pfactory/workspaces/<project_id>/specs/<spec_id>/`). Poll
`mcp__pfactory__task_status` until the task is terminal (`triaged` /
`triaged_empty` / a `*_failed`). If still running, report and let `/loop` come
back next interval.

### 2. Compute the decision

Read the latest run + loop state and decide, in one shot:

```bash
cd apps/backend && python - <<'PY'
import json, sys
from pathlib import Path
from agents.handback.loop import failure_signature, read_loop_state, decide_loop

spec = Path("<spec_dir>")
verdicts = json.loads((spec / "findings" / "verdicts.json").read_text())
cycle, prev = read_loop_state(spec)
cur = failure_signature(verdicts)
d = decide_loop(cycle=cycle, current_failures=cur, previous_failures=prev)
print(json.dumps({"action": d.action, "reason": d.reason, "cycle": cycle,
                  "current": sorted(cur)}))
PY
```

### 3. Act on the decision

- **passed** → stop the loop. Report: tests are green, the feature is fixed.
- **stuck** → stop the loop. Report the reason (cap hit, or no progress) and
  surface `findings/triage_report.md` so a human can take over.
- **retest** →
  1. Hand the correction back — run `/handback-to-aifactory <task_id>` (or, for
     a fully unattended loop, the local send + the cycle bump):
     ```bash
     cd apps/backend && python -m agents.handback <spec_dir> --send
     python - <<'PY'
     from pathlib import Path
     from agents.handback.loop import failure_signature, read_loop_state, record_cycle
     import json
     spec = Path("<spec_dir>")
     cur = failure_signature(json.loads((spec/"findings"/"verdicts.json").read_text()))
     cycle, _ = read_loop_state(spec)
     record_cycle(spec, cycle=cycle + 1, failure_signature=cur)
     PY
     ```
  2. Wait for AIFactory's QA Fixer to finish — poll `mcp__aifactory__task_status`
     on the `aifactory_task_id` from `findings/handback_request.json`.
  3. Re-test in PFactory: `mcp__pfactory__task_rerun(task_id=<task_id>)`.
  4. Return — `/loop` fires the next cycle, which re-evaluates from step 1.

## Running it hands-off

```
/loop 60s /pfactory-fixloop <task_id>
```

Each interval runs exactly one cycle; the loop self-terminates when the decision
is **passed** or **stuck**. A correction send is an opt-in, outward-facing action
— for a truly unattended loop set `PFACTORY_HANDBACK_SEND=1` so the Triager also
auto-prepares, but the *send* in step 3 is still explicit (`--send`).

## Failure modes

- **AIFactory never finishes** → step 3.2 keeps polling; `/loop` bounds wall time
  by interval, and the cycle cap bounds total corrections. Stop manually if the
  fixer is wedged.
- **No `verdicts.json`** → the run didn't produce verdicts (a `*_failed` before
  the Evaluator). Treat as stuck; inspect the logs.
- **Cap reached** → by design — a human reviews `triage_report.md`. Bump
  `PFACTORY_HANDBACK_MAX_CYCLES` only if you understand why it's not converging.

## Non-goals

- Does **not** replace `/handback-to-aifactory` (the single, operator-confirmed
  hand) — it *orchestrates* it on a bound.
- Does **not** remove the human stop-gate: the loop always terminates at the cap
  or on no-progress rather than churning AIFactory runs indefinitely.
