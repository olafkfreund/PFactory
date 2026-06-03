# Hand a correction back to AIFactory (the closed loop)

> **The reverse of the handover.** `/handover-to-pfactory` ships a finished
> feature *to* PFactory for testing; **the hand-back** ships the *problems
> PFactory found* back *to* AIFactory for a fix. Together they close the
> AIFactory ↔ PFactory loop: **handover → test → (fail) → hand-back → AIFactory
> QA Fixer → re-test → done** (epic
> [#182](https://github.com/olafkfreund/PFactory/issues/182)).

This guide is the operator walkthrough. The data path is built end to end; the
*live* send + fixer run needs both apps running (see [End-to-end](#end-to-end)).

## The flow

```
PFactory task reaches a terminal status (triaged) with failing tests
   │
   ├─ Triager completion hook prepares findings/handback_request.{md,json}   (auto, default ON)
   │
   ▼
/handback-to-aifactory  →  preview  →  send (confirm)
   │                                     │
   │                          POST /api/tasks/{id}/apply-correction
   ▼                                     ▼
AIFactory writes QA_FIX_REQUEST.md onto the original spec → runs the QA Fixer
   │
   ▼
re-test (task_rerun) — bounded by /pfactory-fixloop → passed | stuck
```

Nothing is assembled by hand: when the Triager goes terminal with failures, its
completion hook ([#185](https://github.com/olafkfreund/PFactory/issues/185))
builds the correction request and writes it to the workspace. **Preparing is
default ON; sending is opt-in.**

## What gets written

In the PFactory workspace (`~/.pfactory/workspaces/<project>/specs/<spec>/`):

| File | Who writes it | What it is |
|---|---|---|
| `findings/handback_request.md` | Triager hook (#185) | the `QA_FIX_REQUEST.md`-shaped payload AIFactory's QA Fixer reads |
| `findings/handback_request.json` | Triager hook | envelope: `aifactory_task_id`, failing tests, source, `dry_run` |
| `context/source.json` → `aifactory{}` + `correction_cycle` | snapshotter (#183) at handover | the hand-back target + loop state |

On the AIFactory side, the receiver writes `QA_FIX_REQUEST.md` into
`<project>/.aifactory/specs/<spec_id>/` and runs the QA Fixer.

## Operator paths

### A. The skill (recommended)

```
/handback-to-aifactory <task_id>
```

It reads the prepared request, previews the target spec + failing tests, and on
your confirmation sends via the AIFactory MCP tool `task_apply_correction`
(`confirm=false` preview → `confirm=true`).

### B. The local CLI (no AIFactory MCP needed)

```bash
cd apps/backend
python -m agents.handback <spec_dir>          # prepare + preview (no send)
python -m agents.handback <spec_dir> --send    # actually POST to AIFactory
```

Dry-run by default; `--send` is the explicit opt-in. It POSTs to the `api_url`
recorded in `source.json` (default `http://localhost:3101`).

### C. Hands-off, bounded loop

```
/loop 60s /pfactory-fixloop <task_id>
```

One bounded cycle per interval — hand back, wait for the QA Fixer, re-test —
stopping at **passed**, or **stuck** (the correction-cycle cap, default 2, or
the same tests still failing after a correction). The loop can never run away.

## Environment flags

| Flag | Default | Effect |
|---|---|---|
| `PFACTORY_HANDBACK_PREPARE` | **ON** | Triager hook builds + writes the artifact on a failing run. Set falsy to disable. |
| `PFACTORY_HANDBACK_SEND` | **OFF** | the hook also POSTs to AIFactory (opt-in; mirrors `PFACTORY_TRIAGER_GIT_WRITE`). |
| `PFACTORY_AIFACTORY_API_URL` | `http://localhost:3101` | AIFactory web-server base URL for the send. |
| `PFACTORY_HANDBACK_MAX_CYCLES` | `2` | correction-cycle cap before `/pfactory-fixloop` declares `stuck`. |

Per the **no-automatic-pushes** policy, the *send* is always either operator-
confirmed (skill) or an explicit `--send`/opt-in flag. Preparing the artifact is
local and side-effect-free.

## End-to-end

A real round-trip needs **both apps running** + LLM creds + Docker:

1. AIFactory web-server up on **port 3101** (`cd apps/web-server && python -m server.main`), with its LLM provider configured (the QA Fixer runs a real model).
2. The AIFactory receiver present — issue
   [`olafkfreund/AIFactory#317`](https://github.com/olafkfreund/AIFactory/issues/317):
   `POST /api/tasks/{task_id}/apply-correction` + MCP `task_apply_correction`.
3. From PFactory: `/handover-to-pfactory` → let the pipeline run → on a failing
   `triaged`, `/handback-to-aifactory` (or `/pfactory-fixloop`).
4. AIFactory's QA Fixer applies the fix on the original spec; `task_rerun`
   re-tests until green or the cap.

The unit suites (`tests/test_handback_*.py`, AIFactory `tests/test_qa_correction.py`)
cover everything *except* the live fixer run, which is this end-to-end step.

## See also

- `guides/handover-to-pfactory-skill.md` — the forward direction.
- `guides/e2e-smoke.md` — the operator-facing pipeline walkthrough.
- `docs/plans/2026-06-03-aifactory-pfactory-handback-design.md` — the design.
