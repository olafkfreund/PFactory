---
name: handover-to-pfactory
description: Hand a finished feature off to PFactory for autonomous polyglot test generation — from AIFactory, Claude Code, or any tool (via the MCP control plane, or a markdown/Gherkin/EARS acceptance-criteria file ingested with spec_sources.py). Records the task, snapshots the spec, and drives the full Planner → Gen-Functional → Executor → Evaluator → Triager pipeline across the v0.2 5-lane spine (unit / browser / api / integration / mutation) to produce a triage report + (optionally) commit tests to the feature branch + post a PR comment. Pair with /pfactory-watch under /loop to poll the task home and verify coverage — the full round-trip.
when_to_use: When the user has finished an AIFactory feature on a branch and wants PFactory to generate aligned tests + a verdicts/coverage report across pytest / Jest / Playwright as appropriate for each subtask. Common triggers — "hand this off to pfactory", "/handover-to-pfactory", "generate tests for the current PR", "have pfactory test this spec".
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

# /handover-to-pfactory

Hand a finished AIFactory spec off to PFactory.

> **Status (v0.2):** the 4-agent pipeline is wired across the v0.2 5-lane
> spine — **unit** (pytest / Jest / xUnit), **browser** (Playwright /
> Cypress), **api** (httpx / supertest), **integration** (TestContainers /
> WireMock), and **mutation** (mutmut / Stryker). The Planner emits
> polyglot subtasks with explicit `(language, framework)` per subtask, so a
> single plan can mix pytest tests for Python with Jest/Playwright tests
> for TypeScript. Real end-to-end run against an AIFactory project still
> requires credentials for the configured LLM provider + a running Docker
> daemon + a real git/gh setup. See `guides/e2e-smoke.md` for the
> operator-facing walkthrough.
>
> The skill records the task, snapshots the AIFactory spec into
> `~/.pfactory/workspaces/<proj>/specs/<spec>/context/`, and (with
> `PFACTORY_AUTO_*=1`, the production default) auto-fires the pipeline.
> Final status reaches `triaged` / `triaged_empty` when the Triager
> finishes; `findings/triage_report.md` holds the human-readable report.
>
> **Prerequisites (v0.2):**
> - **`.pfactory.yml`** at the AIFactory repo root declaring the targets
>   the pipeline will exercise (HTTP services, k8s contexts, docker-compose
>   stacks, feature-flag overlays). If missing, ask the user to run
>   `/pfactory-init` first.
> - **`.pfactory/tests-catalog.json`** at the AIFactory repo root — the
>   persistent cross-run catalog the Triager consults to decide
>   UPDATE-in-place vs CREATE-new per AC. `/pfactory-init` seeds it empty
>   on first adoption.

## When to use

Trigger this skill when the user signals "ship the tests" or "have
pfactory test this":

- explicit `/handover-to-pfactory`
- "hand this over to pfactory"
- "generate tests for spec X"
- "have pfactory cover this PR"

If the user is mid-feature and the branch isn't ready, push back rather
than handing over a half-built thing.

## Procedure

### 0. Verify v0.2 prerequisites are in place

Before invoking the MCP tool, confirm the AIFactory repo root contains:

```bash
test -f .pfactory.yml && echo "ok: .pfactory.yml"
test -f .pfactory/tests-catalog.json && echo "ok: tests-catalog.json"
```

- If `.pfactory.yml` is missing → tell the user to run `/pfactory-init`
  first. PFactory v0.2 needs declared targets to drive the polyglot
  Planner — without them, the Planner can't assign `target_name` to
  subtasks and the Browser / API lanes have nowhere to run.
- If `.pfactory/tests-catalog.json` is missing → run `/pfactory-init` (it
  seeds an empty catalog) OR write `{"version": 1, "updated_at":
  "<now-Z>", "tests": []}` manually. The catalog lets the Triager decide
  UPDATE-in-place vs CREATE-new per AC. Without it, the Triager treats
  every accepted test as a brand-new CREATE.

These checks ensure the pipeline operates with the v0.2 `(language,
framework)` polyglot mental model. The Planner picks the
`(language, framework)` per subtask — e.g. `(python, pytest)` for a
Python helper, `(typescript, jest)` for a unit-tested React utility,
`(typescript, playwright)` for an end-to-end browser flow. A single
test_plan.json can mix all three.

### 1. Gather the four required arguments

The PFactory MCP tool needs `project_id`, `spec_id`, `branch`, and
`base_ref`. Infer from the conversation + git state; ask only what's
missing.

| Argument | How to determine |
|---|---|
| `project_id` | The AIFactory project ID. Look in the conversation, the AIFactory portal, or `~/.aifactory/projects.json`. If still unclear, ask. |
| `spec_id` | The AIFactory spec ID (the directory name under `~/.aifactory/workspaces/{project_id}/specs/`). Often visible in the recent commits or chat. |
| `branch` | Current branch from `git rev-parse --abbrev-ref HEAD`. |
| `base_ref` | The PR base. Default to `main`; use `git merge-base HEAD origin/main` for the actual fork point if the user pushed back. |

### 1b. Ask what to focus on + whether to enable a visual inspection (#170)

Before previewing, ask the user two things (skip whichever is already clear from
the conversation):

1. **What should PFactory focus on?** — the task intent / acceptance focus. This
   sharpens the work the Planner reads.
2. **Enable a visual inspection?** — for a UI-heavy feature (or a SaaS target
   like ServiceNow), PFactory can record a Playwright **browser** run, capture
   per-step **verification** + **error** screenshots, and package a human
   **visual-inspection report** + correction plan into `automated-test/<datetime>/`
   (committed to the repo, dry-run by default; surfaced in the portal's *Visual
   Reports*). If yes, gather:
   - the **visual target** name (a `visual: true` target in `.pfactory.yml`), and
   - the **flow** to inspect (what the human wants to verify).

Pass these to `task_create_and_run` as the optional `visual_inspection` argument:

```
visual_inspection = { "enabled": true, "target": "<target name>", "flow": "<what to inspect>" }
```

Omit it (or `enabled: false`) for a normal code-test task — the default path is
unchanged.

### 2. Confirm the project is registered with PFactory

Call `mcp__pfactory__project_list`. If the AIFactory project isn't in
the result, register it:

```
mcp__pfactory__project_create(
  id=<project_id>,
  name=<human-readable name>,
  root_path=<absolute path to the local checkout>
)
```

### 3. Preview the handover before committing

Call `task_create_and_run` with `confirm=false` first:

```
mcp__pfactory__task_create_and_run(
  project_id=...,
  spec_id=...,
  branch=...,
  base_ref=...,
  confirm=false
)
```

The response contains the `would_create` workspace path. Show this to
the user. Wait for their go-ahead (or interpret a clear yes from the
trigger phrase: "yes, hand it over").

### 4. Create the task for real

Call the same tool with `confirm=true`. The response contains:

- `task_id` — record this verbatim
- `spec_dir` — the PFactory workspace path (e.g.
  `~/.pfactory/workspaces/<project>/specs/<id>/`)
- `portal_url` — `http://localhost:3114/tasks/<task_id>` (the portal
  ships in Tasks 9-10; until then the URL is a placeholder)

### 5. Report cleanly back to the user

A one-line summary at minimum, e.g.:

> Task `<task_id>` created in PFactory. Workspace at `<spec_dir>`.
> The Planner will emit polyglot subtasks across the unit / browser /
> api / integration lanes (mutation is orthogonal — it strengthens
> whatever else lands). Poll with `mcp__pfactory__task_status` for the
> live status; the final state is `triaged` / `triaged_empty`.

If the user wants progress: call `task_status` once after a beat.

### 6. (Optional) Fetch the report when ready

When pipeline tasks (5-8) are landed, the Triager writes `report.md` +
`report.json` into the workspace. Fetch with:

```
mcp__pfactory__report_get(task_id=<task_id>, format='md')
```

## Failure modes

- **Unknown project** → `project_list` is empty or doesn't contain the
  id. Walk the user through `project_create` first.
- **Spec already handed over** → `task_create_and_run` errors with
  "spec_dir already exists". Offer `task_rerun` instead.
- **PFactory MCP server not reachable** → the tool call times out. Tell
  the user to start the server: `scripts/start-pfactory-mcp.sh` from
  the PFactory repo root. (This skill assumes the AIFactory project's
  `.mcp.json` registers PFactory — see the companion skill at
  `companion-skills/aifactory-handover-to-pfactory/` in the PFactory
  repo for installation steps.)

## Non-goals

- This skill does **not** drive the Planner / Generators / Executor /
  Evaluator / Triager directly — those agents run inside the PFactory
  backend pipeline once the task is created.
- This skill does **not** push code or open PRs by default. The Triager
  side-effects (`git commit` to the feature branch, `gh pr comment`) are
  DRY-RUN by default; operators opt in via
  `PFACTORY_TRIAGER_GIT_WRITE=1` and `PFACTORY_TRIAGER_PR_COMMENT=1`.
- **Non-AIFactory handover is supported** (Claude Code or any tool): provide
  the feature's acceptance criteria as a markdown / Gherkin / EARS file and
  ingest it with `python apps/backend/spec_sources.py <file> --context
  <spec_dir>/context` (see `guides/spec-sources.md`). That writes the canonical
  `context/aifactory_spec.md` the Planner reads, then hand off the same way.
  The AIFactory snapshot path above is the warm-start wedge, not the only
  entry point.
- This skill does **not** create `.pfactory.yml` or
  `.pfactory/tests-catalog.json`. Use `/pfactory-init` for that.
- The `task_create_and_run` MCP tool signature is **unchanged from v0.1**
  — only the downstream pipeline is polyglot. No new arguments are
  required to drive the unit / browser / api / integration lanes; the
  Planner infers `(language, framework)` per subtask from the diff +
  the targets declared in `.pfactory.yml`.

## After handover — watch it home (the round-trip)

Handing off returns a `task_id` but the pipeline runs asynchronously. To close
the loop **hands-off**, chain into the `/pfactory-watch` skill under `/loop`:

```
/loop 30s /pfactory-watch <task_id>
```

`/pfactory-watch` polls `mcp__pfactory__task_status` each interval; when the
task reaches a terminal state (`triaged` / `triaged_empty`, or a `*_failed` /
`stuck`) it **picks up `findings/triage_report.md` and verifies** that the
generated, accepted tests cover the acceptance criteria you handed off — then
stops the loop. This gives you the full round-trip:

```
set goals/ACs → /handover-to-pfactory → (PFactory plans+writes+runs+scores)
   → /loop /pfactory-watch → pick up report → verify coverage → done
```

After running this skill, offer to start the watch loop for the returned
`task_id` unless the user only wanted to fire-and-forget.

## When the tests find problems — hand back for a fix (the reverse direction)

If the watch loop reports a `triaged` run whose report has **failing tests /
rejects** (or a visual-inspection fail), the loop doesn't end at "here are the
problems" — you can hand them straight back to AIFactory for a fix. When the
Triager goes terminal with failures, its completion hook (#185) already writes
the correction request to the workspace
(`findings/handback_request.{md,json}`). Run **`/handback-to-aifactory`** to
preview it and (on confirm) send it — AIFactory's QA Fixer writes the fix on the
original spec. Then re-run PFactory to verify:

```
/handover-to-pfactory → test → (failures) → /handback-to-aifactory
   → AIFactory QA Fixer → task_rerun to verify → done
```

This closes the full AIFactory ↔ PFactory loop (epic #182).
