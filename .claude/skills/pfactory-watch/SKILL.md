---
name: pfactory-watch
description: Poll one PFactory planning session through the pipeline and report where it stands — board column, gate result, blocking findings, and the readiness checks that still fail. One check per invocation, so it can be driven with /loop. Read-only; it never approves, waives, or emits.
when_to_use: After /handover-to-pfactory returns a session_id and you want to follow the session through enrich, feasibility and the review gates until it needs a human. Common triggers — "watch the pfactory session", "/pfactory-watch <session_id>", "is the plan reviewed yet", "did the gates pass", "/loop 30s /pfactory-watch <session_id>".
allowed_tools:
  - mcp__pfactory__plan_status
  - mcp__pfactory__plan_get
  - mcp__pfactory__plan_list
  - Read
  - Bash
---

# /pfactory-watch

Poll one PFactory **planning session** and report its state. This skill does
**one** check per invocation — to make it hands-off, run it under `/loop`:

```
/loop 30s /pfactory-watch <session_id>
```

`/loop` re-fires on the interval; the skill stops the loop as soon as the session
reaches a state that needs a human or is finished.

## Arguments

- `session_id` (required) — the id returned by `/handover-to-pfactory`. If
  omitted, call `mcp__pfactory__plan_list` and ask the user which session to watch.

## Procedure (one pass)

### Step 1 — poll

```
mcp__pfactory__plan_status(session_id: "<id>")
```

Read: `status`, `board_state`, `gates_passed`, `needs_human`, `correlation_key`,
`issue_number`, `aifactory_task_id`.

### Step 2 — classify

| Bucket | `status` | Action |
|---|---|---|
| Running | `ingested`, `processing`, `reviewing` | print a one-line progress update (`status` / `board_state`) and let the loop re-fire |
| Needs a human | `processed` (board `human_review`) | go to Step 3, then STOP the loop |
| Rejected | `rejected` | report the approver's feedback, then STOP the loop |
| Done | `approved`, `emitted` | report the outcome (Step 4), then STOP the loop |

`processed` means the AI work is finished and the session is parked in
`human_review` awaiting sign-off — that is the normal terminal state for this
skill. Approval is a human act, so never call `plan_approve` from the loop.

To stop the loop, finish the turn without scheduling another iteration and say
plainly that the session is terminal.

### Step 3 — report the review

Call `mcp__pfactory__plan_get(session_id)` and summarise:

1. `gates_passed`, the aggregate score, and the per-lens scores (architecture,
   security, best-practice, feasibility).
2. Blocking findings, each **with its citation** — a finding restated without its
   citation is not reportable.
3. The feasibility estimates: coarse cost, effort rollup, IAM access decisions.
4. Every hard readiness check that failed, by name (`children-present`,
   `criteria-present`, `ac-child-coverage`, `deps-sound`, `access-granted`,
   `env-buildable`, `enrichment-integrity`, `language-reconciled`,
   `change-footprint-surfaced`, `constitution-grounded`,
   `deployment-pipeline-present`, `no-blocking-findings`, `decompose-trustworthy`).
   A plan cannot be approved while any of these is failing and unwaived;
   `no-blocking-findings` can never be waived.
5. The concrete next step for the user: approve, fix the plan and re-ingest, or
   record a waiver. Present it, do not act on it.

### Step 4 — on approved / emitted

- `approved` — gates passed and a human signed off. The next step is emit
  (`/handover-to-pfactory` Step 6), which is dry-run by default.
- `emitted` — report `issue_number`, `correlation_key` and `aifactory_task_id` so
  the user can follow the work into AIFactory and TFactory under one key.

## Notes

- Strictly read-only. `plan_status` / `plan_get` / `plan_list` have no side
  effects; this skill never approves, waives, rejects or emits.
- Cadence: `30s` is a good default. `plan_process` does live-cloud enrichment,
  reconnaissance and LLM review lenses, so a session can sit in `processing` or
  `reviewing` for minutes.
- Host without MCP: `GET /api/plan/sessions/{id}` returns the same view. See
  `guides/agent-handoff.md`.
- Pairs with `/handover-to-pfactory` — that skill hands the plan over and returns
  the `session_id`; this one follows it to the human gate.
