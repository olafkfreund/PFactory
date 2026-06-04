---
name: demo
description: Produce a realistic, multi-pane screencast demo of the Claude Code → PFactory handover workflow, end to end and the same quality every time. Picks a known-good scenario (greeting-generator / python-unit / multi-lane / failure-flow), seeds its spec, starts the portal + backend, hands the feature off to PFactory, runs the full Planner → Gen-Functional → Executor → Evaluator → Triager pipeline, records the Claude Code terminal (asciinema) and the PFactory portal (Playwright) in lockstep, stitches them with the triage report into one composite demo.mp4 + demo.gif, and refuses to publish until an automated quality gate passes. Use to make demos.
when_to_use: When the user wants to create or refresh a PFactory demo / screencast / showcase — a realistic over-the-shoulder recording of a developer handing a finished feature to PFactory and seeing tests generated, run, scored (pass AND fail), then merged or dismissed. Triggers — "/demo", "make a demo", "record a screencast", "showcase the handover", "demo the portal", "create a pfactory demo".
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Skill
  - mcp__pfactory__project_list
  - mcp__pfactory__project_create
  - mcp__pfactory__task_create_and_run
  - mcp__pfactory__task_status
  - mcp__pfactory__report_get
---

# /demo

Produce a realistic, repeatable, multi-pane screencast of the **Claude Code →
PFactory** handover — a developer finishes a feature, hands it off, PFactory
plans + generates + runs + scores tests (showing both a **pass** and a **fail**),
and the human merges the good tests / dismisses the bad run. The output is one
composite `demo.mp4` + `demo.gif` that clears a hard quality gate every time.

```
┌────────────┬───────────────┐
│ Claude Code│  PFactory     │   top: terminal handover (asciinema → video)
│ terminal   │  portal       │        + portal walkthrough (Playwright)
├────────────┴───────────────┤
│  triage report / verdicts  │   bottom: the verdicts a human acts on
└────────────────────────────┘
        stitched by ffmpeg → demo.mp4 + demo.gif
```

## Inputs

- **scenario** (arg, optional): one of `greeting-generator` (default),
  `python-unit`, `multi-lane`, `failure-flow`. See
  [`scenarios.md`](scenarios.md) for each story, its ACs, and expected verdicts.
- **`--no-publish`**: stop after the quality gate; don't touch `docs/`.
- **`--base <scenario>`**: only for `failure-flow` (default `greeting-generator`).

If the user just says "make a demo", pick `greeting-generator` and say so.

## Building blocks (reused, not reinvented)

| Step | Tool |
|---|---|
| Seed any scenario (spec + SUT repo) | `scripts/demo/seed-scenario.sh <scenario>` |
| Hand off to PFactory | `/handover-to-pfactory` skill (MCP control plane) |
| Watch the pipeline home | `/pfactory-watch` skill under `/loop` |
| Record terminal pane | `scripts/record-handover-demo.sh` under `asciinema rec` |
| Record portal pane | `scripts/record-portal-walkthrough.ts` (Playwright) |
| Render `.cast` → video | `scripts/demo/cast-to-video.sh` |
| Composite the panes | `scripts/demo/compose.sh` |
| Enforce quality | `scripts/demo/quality-gate.sh` |

---

## Procedure

Follow the PARR cadence (this repo's house rule): announce each step, run it,
verify the checkpoint, then continue. **Never** skip the quality gate.

### 0. Set up the run directory

Pick a run id (scenario + a short stamp the user gives, or a counter — do
**not** invent a timestamp). Everything for this demo lives under it:

```bash
RUN_DIR="docs/static/recordings/demos/<scenario>-<runid>"
mkdir -p "$RUN_DIR/panes"
```

`panes/` will hold `terminal.webm`, `portal.webm`, and `report.{png,webm}`.

### 1. Preflight — tools + services

```bash
for t in ffmpeg ffprobe asciinema node npx; do command -v "$t" >/dev/null || echo "MISSING: $t"; done
command -v agg >/dev/null || echo "note: agg absent — terminal pane uses the Playwright fallback (needs network once)"
```

Confirm the portal + backend are up (the recorders need them):

- Backend (API) on `http://localhost:3114`, frontend on `http://localhost:3110`
  (or `:3115` for the dev server — check `record-portal-walkthrough.ts`).
- `~/.pfactory/.token` exists.

If they're not running, start them per the project README
(`apps/web-server && .venv/bin/python -m server.main`, then
`apps/frontend-web && npm run dev`) and **ask the user before** killing any
already-running backend — it may hold an `ANTHROPIC_API_KEY` you don't want to
swap. For a subscription run, follow §5 of
`docs/plans/2026-05-29-pfactory-demo-showcase-design.md` (unset
`ANTHROPIC_API_KEY`, set the `PFACTORY_AUTO_*=1` flags, verify the SDK picks up
the OAuth token; fall back to `CLAUDE_CODE_OAUTH_TOKEN=$(claude setup-token --print)`).

**Checkpoint:** all tools present; portal `/` returns 200; token file exists.

### 2. Seed the scenario

One command seeds any of the four scenarios — it writes the AIFactory spec
workspace and, for local SUTs (`python-unit` / `multi-lane`), materialises the
finished feature into a git repo with a clean base→branch diff, a
`.pfactory.yml`, and an empty `tests-catalog.json`:

```bash
eval "$(scripts/demo/seed-scenario.sh "$SCENARIO" | sed 's/^/export DEMO_/')"
# exports DEMO_PROJECT_ID, DEMO_SPEC_ID, DEMO_REPO_PATH, DEMO_BRANCH,
# DEMO_BASE_REF, DEMO_LANES, DEMO_EXPECT_FAILURE
```

`greeting-generator` delegates to `scripts/seed-aifactory-workspace.sh` (its SUT
is the external `olafkfreund/pfactory-demo` repo + Pages deploy — see the design
doc). `failure-flow` seeds its base (default `greeting-generator`, override with
`--base`) and sets `PFACTORY_TRIAGER_GIT_WRITE=1`. See [`scenarios.md`](scenarios.md).

**Checkpoint:** the emitted `DEMO_REPO_PATH` (when local) is a git repo whose
`DEMO_BASE_REF..DEMO_BRANCH` diff contains the feature, and the AIFactory
workspace has a parseable `spec.md` + `implementation_plan.json`.

### 3. Start the two pane recorders, then fire the handover

Record the panes **concurrently** with the pipeline so the portal animation and
the terminal narration line up with real phase transitions.

1. **Portal pane** (background): run `scripts/record-portal-walkthrough.ts`
   (Playwright) pointed at the demo project; it captures Backlog → In Progress →
   AI Review → Human Review and drops a `.webm`. On NixOS set
   `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to the nix Chrome (see the script header).
2. **Terminal pane** (recorded): wrap `scripts/record-handover-demo.sh` in
   `asciinema rec "$RUN_DIR/panes/terminal.cast" -c scripts/record-handover-demo.sh`.
   For `python-unit` / `multi-lane`, narrate that scenario's handover instead.
3. **Fire the handover** via the `/handover-to-pfactory` skill (or
   `mcp__pfactory__task_create_and_run`). Record the `task_id`.
4. **Watch it home**: `/loop 30s /pfactory-watch <task_id>` (or poll
   `mcp__pfactory__task_status`) until `triaged` / `triaged_empty`.

For `failure-flow` only: export `PFACTORY_TRIAGER_GIT_WRITE=1` before firing so
the merge of accepted tests is real on the demo branch. **Never** set
`PFACTORY_TRIAGER_PR_COMMENT=1` for a demo.

**Checkpoint:** task reaches `triaged`; `findings/triage_report.md` exists with
the verdict mix [`scenarios.md`](scenarios.md) predicts.

### 4. Capture the report pane

Screenshot the rendered triage report for the bottom panel:

```bash
# preferred: portal task-detail report view via Playwright screenshot
#   → "$RUN_DIR/panes/report.png"
# fallback: render findings/triage_report.md to an image, or
#   pretty-print it to a terminal and screenshot that.
```

Copy the run's `findings/triage_report.md` into `$RUN_DIR/` too, so the quality
gate can assert the verdicts.

**Checkpoint:** `panes/report.png` (or `report.webm`) exists and is non-empty.

### 5. Render the terminal cast → video

```bash
scripts/demo/cast-to-video.sh "$RUN_DIR/panes/terminal.cast" "$RUN_DIR/panes/terminal.webm"
```

**Checkpoint:** `panes/terminal.webm` non-empty.

### 6. Composite the panes

```bash
RUN_DIR="$RUN_DIR" TITLE="PFactory — <scenario> (Claude Code handover)" \
  scripts/demo/compose.sh
```

Produces `$RUN_DIR/demo.mp4` + `$RUN_DIR/demo.gif`.

**Checkpoint:** both files exist and are non-empty.

### 7. **Quality gate — the bar (mandatory)**

```bash
RUN_DIR="$RUN_DIR" MIN_SECONDS=15 \
  EXPECT_FAILURE=<1 if the scenario seeds a failure, else 0> \
  scripts/demo/quality-gate.sh
```

It checks: mp4 decodes + is long enough + 16:9 ≥1280w · gif present · both panes
captured · report panel present · triage_report.md has verdicts · and (when the
scenario seeds a failure) that **both a pass and a fail** are in the report.

**If the gate fails: do NOT publish.** Diagnose (REVISE), re-record the weak
pane, recomposite, re-gate. A demo only ships once this exits 0.

### 8. Publish (skip with `--no-publish`)

- The deliverables are `$RUN_DIR/demo.mp4` + `demo.gif`. Surface them to the
  user with `SendUserFile` (proactive).
- Optional Pages showcase: embed the GIF + link the MP4 into `docs/showcase.md`
  (see the design doc for the section shape) and the hero CTA on
  `docs/index.md`. Preserve `docs/_config.yml`'s `remote_theme` line — dropping
  it has broken Pages builds before.
- Per saved guidance, after a demo lands also refresh README / docs / CHANGELOG
  if this demo represents a shipped capability.

### 9. Report back

One scannable summary: scenario, verdict mix, demo length, the two output
paths, and (if published) the showcase URL. State plainly that the quality gate
passed — quote the PASS/FAIL line.

---

## What "good quality each time" means

The gate in step 7 is the contract. A demo that ships always has:

1. a **multi-pane** frame — terminal + portal + verdicts, never a single window;
2. a **real pipeline run** — real Planner/Gen-Functional/Executor/Evaluator/
   Triager output, not a mock;
3. the **pass-and-fail** beat — at least one accept and (for failure scenarios)
   one reject/flag visible in the report panel;
4. a **coherent user story** — dev finishes work → hands off → tests run → human
   sees verdicts → merge/dismiss;
5. **web-embeddable** outputs — H.264 mp4 (+faststart) and a palette-optimised
   gif at a readable pace.

## Failure modes

- **Backend on API keys, not subscription** → swap per design-doc §5 (ask first
  before killing a running backend).
- **Pipeline ends `*_failed` / `stuck`** → don't record a broken run; read
  `logs/<agent>.log`, fix the seed/spec, rerun. The gate would reject it anyway.
- **`AC#5` (or the seeded failure) unexpectedly passes** → the SUT bug
  regressed; re-seed. With `EXPECT_FAILURE=1` the gate fails loudly so you can't
  ship a demo missing its failure beat.
- **Terminal cast won't render** → install `agg`
  (`nix shell nixpkgs#asciinema-agg`) or ensure network for the asciinema-player
  fallback; see `cast-to-video.sh`.
- **Playwright Chromium fails on NixOS** → set
  `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to the nix-managed Chrome.
- **ffmpeg concat/codec drift** → `compose.sh` re-encodes (never `-c copy`) and
  normalises every pane, so mismatched pane resolutions are handled.

## Non-goals

- Not a live "human at the keyboard" capture — this is the deterministic
  **composite** path (asciinema + Playwright + ffmpeg). For real desktop capture
  use OBS separately.
- Does not post PR comments. Demo runs never set
  `PFACTORY_TRIAGER_PR_COMMENT=1`. Only `failure-flow` opts into a **local**
  git write so the merge beat is real.
- Does not create `.pfactory.yml` / `tests-catalog.json` — that's `/pfactory-init`.
