---
name: handback-to-aifactory
description: When PFactory's tests find problems in a feature, hand a correction back to AIFactory for revision. Reads the correction request PFactory's Triager already prepared (findings/handback_request.{md,json}), previews the target AIFactory spec, and on confirmation sends it so AIFactory's QA Fixer writes the fix on the original spec. The reverse direction of /handover-to-pfactory — together they close the AIFactory ↔ PFactory loop (epic #182).
when_to_use: When a PFactory task has finished (triaged) with failing tests / rejects, or a visual-inspection failed, and the user wants AIFactory to fix the code under test. Triggers — "hand this back to aifactory", "/handback-to-aifactory", "send the failures back for a fix", "have aifactory correct this".
allowed-tools:
  - mcp__aifactory__task_apply_correction
  - mcp__aifactory__task_status
  - mcp__pfactory__task_status
  - mcp__pfactory__report_get
  - Bash
  - Read
---

# /handback-to-aifactory

Hand a PFactory correction back to AIFactory for revision. This is the
**reverse** of `/handover-to-pfactory`: that one ships a finished feature *to*
PFactory for testing; this one ships the *problems PFactory found* back *to*
AIFactory for a fix.

> **Where the payload comes from:** you don't assemble anything by hand. When a
> PFactory task reaches a terminal status with failures, the Triager's
> completion hook (#185) already builds the correction request and writes it to
> the workspace:
>
> - `findings/handback_request.md`   — the `QA_FIX_REQUEST.md`-shaped payload
> - `findings/handback_request.json` — the structured envelope (target spec,
>   failing tests, source)
>
> Preparing is **default ON**; **sending is opt-in**. This skill is the operator
> path that previews then sends.

## When to use

Trigger when a tested feature has problems to fix:

- explicit `/handback-to-aifactory <task_id>`
- "hand this back to aifactory" / "send the failures back for a fix"
- after `/pfactory-watch` reports a `triaged` run whose report has rejects

If the run is clean (`triaged_empty`, or all-accept), there's nothing to hand
back — say so and stop.

## Procedure

### 1. Locate the finished task workspace

You need the PFactory spec dir for the task, e.g.
`~/.pfactory/workspaces/<project_id>/specs/<spec_id>/`. Infer the `task_id`
from the conversation (the value `/handover-to-pfactory` returned), or ask.
Confirm the run is terminal with `mcp__pfactory__task_status`.

### 2. Check a hand-back was prepared

```bash
test -f <spec_dir>/findings/handback_request.md \
  && echo "ok: handback prepared" \
  || echo "no handback artifact — run had no failures, or PREPARE was disabled"
```

- If the artifact is missing because the run was clean → nothing to do.
- If it's missing because `PFACTORY_HANDBACK_PREPARE` was disabled, you can
  generate it now with the local CLI (preview only):

  ```bash
  cd apps/backend && python -m agents.handback <spec_dir>
  ```

### 3. Preview the correction + the target

Read the payload and show the user **what will be sent** and **where**:

```bash
cat <spec_dir>/findings/handback_request.md            # the fix request
```

Read `findings/handback_request.json` for the target — `aifactory_task_id`
(the `<project_id>:<spec_id>` of the original AIFactory spec) and the list of
failing tests. Summarise: "N failing tests → AIFactory spec `proj:spec`."

### 4. Confirm, then send

Sending kicks off a paid AIFactory agent run, so it's **confirm-first**. Two
equivalent paths:

**a) Via the AIFactory MCP tool (preferred — symmetric with the handover):**

First preview (writes/starts nothing on AIFactory's side):

```
mcp__aifactory__task_apply_correction(
  project_id=<from aifactory_task_id>,
  spec_id=<from aifactory_task_id>,
  fix_request_md=<contents of handback_request.md>,
  source=<"triage" | "visual_inspection", from the json>,
  confirm=false
)
```

Show the operator the preview (`would_write`). On a clear yes, call again with
`confirm=true` — AIFactory writes `QA_FIX_REQUEST.md` onto the original spec and
runs its QA Fixer.

**b) Local fallback (when the AIFactory MCP server isn't registered):**

```bash
cd apps/backend && python -m agents.handback <spec_dir> --send
```

This POSTs to AIFactory's `POST /api/tasks/{task_id}/apply-correction` using the
`api_url` recorded in `source.json` (default `http://localhost:3101`, override
with `PFACTORY_AIFACTORY_API_URL`).

### 5. Report back

One line: the AIFactory `task_id` the correction went to + its returned status
(e.g. `qa_fixing`). If you want to watch AIFactory finish, poll
`mcp__aifactory__task_status`.

## Closing the loop (re-test after the fix)

Once AIFactory's QA Fixer reports done, re-run PFactory on the same spec to
verify the fix actually passes:

```
mcp__pfactory__task_rerun(task_id=<pfactory task_id>)
```

The bounded, hands-off version of this cycle (poll AIFactory → auto re-test,
stop at a correction-cycle cap) is the `/pfactory-fixloop` skill (epic #182 P6).

## Failure modes

- **No handback artifact** → the run was clean, or PREPARE was disabled. Use the
  CLI in step 2 to generate a preview, or stop.
- **AIFactory unreachable** → the send returns `ok:false` with an error; the
  artifact stays on disk. Start AIFactory's web-server (port 3101) and retry.
- **Spec not found on AIFactory** (404) → the original spec was deleted/renamed.
  Check `aifactory_task_id` in the json against AIFactory's specs.
- **AIFactory MCP tool not available** → use the local CLI fallback (4b), or
  register AIFactory's MCP server (see the companion skill).

## Non-goals

- Does **not** assemble the correction — the Triager's hook (#185) does that.
- Does **not** push code or merge anything; it triggers AIFactory's QA Fixer,
  which makes its own changes on the AIFactory feature branch.
- Does **not** auto-send — every send is operator-confirmed (or the explicit
  `--send` flag), per the no-automatic-pushes policy.
