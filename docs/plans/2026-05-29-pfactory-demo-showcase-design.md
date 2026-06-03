# PFactory v0.2.0 demo + showcase — design

> **Date:** 2026-05-29
> **Status:** Approved (super-brainstorm), awaiting reviewer subagent +
> user gate before execution
> **Authored via:** `/super-brainstorm` with `ultrathink`
> **Target release:** `v0.2.0` (already shipped — this builds on top)

## Summary

End-to-end PFactory v0.2.0 demo + Pages showcase. We ship a small Vite +
React web app to a new public repo, hand a five-AC spec off to PFactory's
4-agent pipeline (with one AC seeded to fail), capture portal +
Playwright evidence as the run executes, stitch the evidence into a
short GIF + MP4, and publish all of it as a new `/showcase/` page on the
PFactory GitHub Pages site so anyone landing on the site sees the
pipeline running end-to-end with real artefacts.

The demo uses the user's **existing Claude subscription**
(`CLAUDE_CODE_OAUTH_TOKEN`), not API keys. Zero per-run cost.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  olafkfreund/pfactory-demo (public)                                      │
│  Vite + React + TS  →  GitHub Actions deploy.yml  →  gh-pages            │
│                     →  https://olafkfreund.github.io/pfactory-demo/      │
│  .pfactory.yml → declares the Pages URL as the Browser-lane target       │
└──────────────────────────────────────────────────────────────────────────┘
                                       ↑
                                       │ AIFactory handover (simulated)
                                       │
┌──────────────────────────────────────────────────────────────────────────┐
│  ~/.aifactory/workspaces/pfactory-demo/specs/001-greeting-generator/      │
│    spec.md                  (user story + 5 ACs)                          │
│    implementation_plan.json (1 phase per AC)                              │
└──────────────────────────────────────────────────────────────────────────┘
                                       ↓
┌──────────────────────────────────────────────────────────────────────────┐
│  PFactory portal (localhost:3110)                                         │
│    mcp__pfactory__task_create_and_run                                     │
│      → snapshotter → ~/.pfactory/workspaces/.../context/                  │
│      → Planner (real LLM via subscription)                                │
│      → Gen-Functional (real LLM) → tests/e2e/*.spec.ts in demo repo       │
│      → Executor (Playwright in Docker against Pages URL)                  │
│        → findings/evidence/<test_id>/{screenshots,video.webm,trace.zip}   │
│      → Evaluator (real LLM)                                               │
│      → Triager → triage_report.md (with evidence-link bullets from #32)   │
└──────────────────────────────────────────────────────────────────────────┘
                                       ↓
┌──────────────────────────────────────────────────────────────────────────┐
│  PFactory Pages: docs/showcase.md  (nav_order: 6, permalink /showcase/)  │
│    Embedded GIF (portal phases) + MP4 + triage report + evidence links    │
│    Media hosted in olafkfreund/pfactory-demo gh-pages /showcase-evidence/ │
└──────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. The demo app (SUT) — `olafkfreund/pfactory-demo`

- **Stack:** Vite 7 + React 19 + TypeScript + Tailwind v4
- **UI:** two `<select>` dropdowns (`category` ∈ `{greeting, joke, fortune,
  quote, fact}`, `tone` ∈ `{formal, casual, snarky}`), `Generate` button,
  `Clear` button, output `<div data-testid="output">`
- **Logic:** `src/data.ts` carries a `Record<Category, Record<Tone, string[]>>`
  lookup; `src/generate.ts` exposes `generate(cat, tone): string`
- **Seeded bug for AC#5:** `generate()` memoises by `(category, tone)` key
  and returns the cached value on subsequent calls — clicking Generate
  twice without changing dropdowns or hitting Clear yields the same
  text. AC#5's test catches this; the Triager surfaces it as `reject`.
- **Deployment:** `.github/workflows/deploy.yml` builds on push to `main`,
  publishes to `gh-pages` via `actions/deploy-pages@v4`. Visibility:
  public. Pages enabled with source: `gh-pages` branch.
- **Test selectors:** every interactive + assertable element gets a
  `data-testid` so the Planner+Gen-Functional can write stable selectors.

### 2. User story + 5 ACs (the `spec.md`)

> **Story:** As a visitor on the demo page, I want to pick a category and
> tone and click Generate so I see text matching my selection appear in
> the output panel; clicking Clear empties it; clicking Generate again
> gives me a fresh, different result.

| AC | What | Expected verdict |
|---|---|---|
| AC#1 | Clicking Generate produces non-empty text in `[data-testid=output]` | `accept` |
| AC#2 | When `category=greeting`, output contains one of `hello`/`hi`/`greetings`/`welcome` | `accept` |
| AC#3 | When `tone=snarky`, output contains one of `obviously`/`whatever`/`sure`/`fine` | `accept` |
| AC#4 | Clicking Clear empties `[data-testid=output]` | `accept` |
| AC#5 | Two consecutive Generate clicks (same dropdowns, no Clear) produce **different** text | `reject` — seeded bug |

### 3. AIFactory workspace simulation

- Path: `~/.aifactory/workspaces/pfactory-demo/specs/001-greeting-generator/`
- `spec.md`: the user story + 5 ACs (markdown rendered)
- `implementation_plan.json`: `{ "phases": [ { "id": "AC#1", "name": "…" }, … 5 entries ] }`
  — minimal but well-formed; the Planner mostly drives off `spec.md`
- Created by a small bash helper `scripts/seed-aifactory-workspace.sh`
- Note: this is the documented `PFACTORY_AIFACTORY_ROOT` override path
  (defaults to `~/.aifactory`); the snapshotter (Task 4) handles it
  without any PFactory code changes

### 4. `.pfactory.yml` (in the demo repo)

```yaml
version: 1
default_target: web
targets:
  - name: web
    type: http
    base_url: https://olafkfreund.github.io/pfactory-demo/
    health_check:
      path: /
      expect_status: 200
      timeout_seconds: 30
    selectors_hint: data_testid
```

One target. No auth (public Pages site). Browser lane only at v0.2.0
MVP. `selectors_hint: data_testid` tells the Planner the SUT exposes
stable test ids → better generated selectors.

### 5. Backend restart for the demo run

The currently-running backend (PID surfaced at `/proc/<pid>/environ`)
has `ANTHROPIC_API_KEY` inherited from the parent shell + auto-fire
flags OFF (`PFACTORY_AUTO_PLAN=0`). To run the demo on the subscription:

```bash
# Stop the existing backend background job, then:
unset ANTHROPIC_API_KEY ANTHROPIC_API_KEY_FILE
export PFACTORY_AUTO_PLAN=1
export PFACTORY_AUTO_GENERATE=1
export PFACTORY_AUTO_EVALUATE=1
export PFACTORY_AUTO_TRIAGE=1
# OAuth token: glue-phase step 2.5 verifies the SDK picks it up
# from ~/.claude/.credentials.json automatically. If a smoke
# `python -c "from claude_agent_sdk import ..."` raises an auth
# error, fall through immediately to:
#   export CLAUDE_CODE_OAUTH_TOKEN=$(claude setup-token --print)
# Do NOT wait until mid-pipeline to discover the auth gap.
cd apps/web-server && .venv/bin/python -m server.main &
```

Frontend at `:3110` reconnects automatically. The portal-WebSocket UI
keeps streaming events; the LaneStatusGrid lights up phase-by-phase
exactly as if a production handover fired.

### 6. Pipeline run (real Claude calls)

1. **Trigger** (recorded path): user clicks **New Task** in the portal,
   selects the demo project, picks the spec_id `001-greeting-generator`,
   confirms. Fallback path: a `curl` against `/api/pfactory/tasks`
   (same shape as the MCP tool).
2. **Snapshotter** lifts `spec.md` + `implementation_plan.json` from
   `~/.aifactory/.../001-greeting-generator/` into
   `~/.pfactory/workspaces/pfactory-demo/specs/001-greeting-generator/context/`
   plus `.pfactory.yml` + (empty initial) `tests-catalog.json` from the
   demo repo.
3. **Planner** (real LLM via subscription) reads context, emits 5
   subtasks tagged `(language: typescript, framework: playwright,
   lane: browser, target_name: web, intent: create)`. One subtask per AC.
4. **Gen-Functional** (real LLM, one session per subtask) writes 5
   `.spec.ts` files under `tests/e2e/` in the demo repo on a branch
   `pfactory/handover-001-greeting-generator`. Files committed by the
   git_writer at the end (dry-run by default, gated by
   `PFACTORY_TRIAGER_GIT_WRITE=1`).
5. **Executor** spins `pfactory-runner-playwright:latest`; for each
   subtask sets `extra_env={"PFACTORY_TARGET_URL":
   "https://olafkfreund.github.io/pfactory-demo/"}`. Playwright config
   auto-emits screenshots, video, trace.
6. **Evidence** captured into
   `findings/evidence/<test_id>/{screenshots/, video.webm, trace.zip,
   network.har}` for each of the 5 tests.
7. **Evaluator** scores 5 verdicts: 4 × accept + 1 × reject (AC#5).
   `coverage_delta = null` per Decision 11 (Browser lane).
8. **Triager** dedups + ranks; for each accepted/flagged test calls
   `_collect_evidence_urls` (from commit `5d8f588` — the post-batch
   follow-up) and writes `findings/triage_report.md` with evidence-link
   bullets per candidate.

**Wall-clock estimate:** 3–5 min once Pages is deployed and backend is
restarted.

### 7. Recording mechanism

#### 7a. Playwright auto-video (already shipped)

Task 16 ships `apps/backend/agents/evidence/playwright.config.tmpl.ts`
with `video: 'retain-on-failure'`, `trace: 'on-first-retry'`,
`screenshot: 'only-on-failure'`. The runner image already mounts the
evidence dir. **Zero new code** for SUT capture.

#### 7b. Portal screenshots at phase boundaries

New script: `scripts/showcase-portal-recorder.ts` (Playwright headless,
Node-runnable):

```typescript
// Subscribes to ws://localhost:3102/ws/events?token=<token>
// On each phase transition for the target spec_id, opens
// http://localhost:3110, navigates to the task detail view,
// screenshots:
//   - [data-testid=lane-status-grid]
//   - [data-testid=task-detail-summary]
// Writes to spec_dir/findings/portal-screenshots/{seq}-{phase}.png
// Idempotent; can be re-run; numbered by seq for ffmpeg ordering.
```

Phases captured (in order): `pending → planning → planned → generating
→ generated → executing → evaluating → evaluated → triaging → triaged`.
~10 screenshots total. Recorder is idempotent — captures whatever
phases the backend actually emits.

#### 7c. Stitching

`ffmpeg` concat with a generated input list:

```bash
# Portal time-lapse: 10 PNGs at 0.4 fps (~25s loop, comfortable read pace)
ffmpeg -framerate 0.4 -pattern_type glob -i \
  "$EV_DIR/portal-screenshots/*.png" -vf "scale=1280:-2" \
  -y portal-phases.gif

# Full MP4: portal time-lapse + Playwright videos appended for each test.
# Re-encode to a uniform codec (NOT -c copy) — Playwright videos can vary
# in viewport / resolution per test and concat -c copy fails silently
# on codec/parameter drift.
ffmpeg -f concat -safe 0 -i media-list.txt \
  -c:v libx264 -preset fast -crf 23 -c:a aac -movflags +faststart \
  -y showcase-full.mp4
```

Two outputs: a 25-second GIF for embed + a 2-3-minute MP4 with the SUT
runs included for the link-out.

### 8. Pages showcase — `docs/showcase.md`

```markdown
---
layout: default
title: Demo & Showcase
permalink: /showcase/
nav_order: 6
---

# v0.2.0 in action

## The demo app
## The user story
## The pipeline running                  ← GIF embed + MP4 link
## What got generated                    ← link to demo PR
## The test evidence                     ← video for AC#5 + thumbnails
## The verdict (what humans see)         ← triage_report.md inline
## Reproduce it yourself                 ← numbered commands
```

- **Media hosting:** demo repo's `gh-pages` branch under
  `/showcase-evidence/` (free CDN, public URLs, zero PFactory repo clutter).
- **Cross-link:** new hero CTA on `docs/index.md`: "See the demo →
  /showcase/" prepended to the existing v0.2.0 release CTA.
- **Pages constraint preserved:** `docs/_config.yml`'s
  `remote_theme: pages-themes/cayman@v0.2.0` line stays intact — per
  saved memory, dropping it broke Pages builds during the v0.2 sweep.

## Parallel execution plan

After this spec is approved + reviewer-checked + user-gated, dispatch
four subagents in **one parallel message**:

| # | Subagent | Scope |
|---|---|---|
| **A** | `frontend-developer` | Build + push `olafkfreund/pfactory-demo`. Scaffold Vite+React+TS, write SUT incl. the AC#5 cache bug, **author `.pfactory.yml` directly in the repo** (the spec template is inlined in A's brief — no cross-subagent dependency), write `.github/workflows/deploy.yml`, run `gh repo create --public`, push, poll Pages URL until 200. |
| **B** | `general-purpose` | Author `spec.md` + `implementation_plan.json` + `seed-aifactory-workspace.sh`. (Pre-reviewer this also owned `.pfactory.yml`; advisory recommendation moved that to A to remove cross-agent coupling.) |
| **C** | `typescript-pro` | Write `scripts/showcase-portal-recorder.ts` (Playwright headless, WebSocket listener, phase-transition screenshot loop). |
| **D** | `docs-architect` | Write `docs/showcase.md` shell with all sections + placeholder media slots + update `docs/index.md` hero CTA. |

After parallel phase completes, **sequential glue phase** (me orchestrating):

1. Wait for A's Pages URL → 200 (poll every 5 s, max 5 min)
2. Restart backend per §5 (subscription, auto-fire on)
2.5. **OAuth smoke check** before triggering: run
   `apps/web-server/.venv/bin/python -c "from claude_agent_sdk \
   import ClaudeSDKClient; c = ClaudeSDKClient(); print('auth OK')"`
   — if it raises, immediately export `CLAUDE_CODE_OAUTH_TOKEN=$(claude
   setup-token --print)` and restart. Don't proceed without confirmation.
3. Run B's seed script
4. User clicks **New Task** in the portal (or curl fallback)
5. Run C's recorder concurrently with the pipeline
6. Wait for `status=triaged`
7. ffmpeg → showcase.gif + showcase.mp4
8. `rsync` evidence + GIF + MP4 → pfactory-demo:/gh-pages/showcase-evidence/
9. Render `triage_report.md` → inject into D's `showcase.md`
10. Push docs change → PFactory Pages rebuilds
11. Hard-refresh `https://olafkfreund.github.io/PFactory/showcase/` → verify
   (case-exact: `PFactory` matches the repo name, `showcase` is the
   permalink — both produce the live URL; mismatched case has bitten
   v0.2 sweeps before, so the verification step is hard-coded)

**Wall-clock end-to-end:** 15–20 min from spec approval to live showcase.

## Data model

### `spec.md` shape (AIFactory's contract)

Mirrors the existing `tests/fixtures/planner_smoke/aifactory_spec.md`
format: a user story header, an Acceptance Criteria section with
numbered ACs, optional "Out of scope" + "Notes" tail. The Planner reads
this verbatim.

### `implementation_plan.json` shape

```json
{
  "version": 1,
  "phases": [
    { "id": "AC#1", "name": "Generate produces non-empty text", "status": "complete" },
    { "id": "AC#2", "name": "Greeting category vocabulary",      "status": "complete" },
    { "id": "AC#3", "name": "Snarky tone vocabulary",            "status": "complete" },
    { "id": "AC#4", "name": "Clear empties output",              "status": "complete" },
    { "id": "AC#5", "name": "Two clicks yield different text",   "status": "complete" }
  ]
}
```

All phases marked `complete` because the feature is "done" from the
AIFactory perspective — PFactory's job is only to test it.

### Test artefacts

- `tests/e2e/ac1-generate-non-empty.spec.ts` (and 4 more)
- `tests/e2e/playwright.config.ts` (PFactory-tuned via the template)
- `tests-catalog.json` populated on first run

### Evidence artefacts (per Task 16)

`spec_dir/findings/evidence/<test_id>/{screenshots/*.png, video.webm,
trace.zip, network.har}` for each of the 5 tests.

## Error handling

- **Pages deploy stuck (no 200 within 5 min):** glue phase step 1
  surfaces the deploy log link + aborts. User fixes the deploy, re-runs
  glue.
- **Backend won't restart on the subscription path:** log shows
  `CLAUDE_CODE_OAUTH_TOKEN missing` or similar; user runs
  `claude setup-token` and we retry.
- **Planner emits 0 subtasks (`status=planned_empty`):** Triager produces
  an empty report; showcase has nothing to embed. Action: investigate
  `logs/planner.log`, likely a malformed `spec.md`.
- **AC#5 unexpectedly passes:** the SUT bug regressed or wasn't seeded;
  recording proceeds and we publish anyway, just note in showcase.md
  that the failing-case visual is currently absent (we file a
  follow-up).
- **Playwright runner image missing:** Task 7 should have built it;
  check `docker image ls pfactory-runner-playwright`. Build manually
  via the Task 7 Dockerfile if needed.
- **Recorder script crashes mid-run:** ffmpeg step uses whatever
  screenshots landed; if fewer than 3 phases were captured, fall back
  to a static still-image strip instead of an animated GIF.

## Testing strategy

This **is** the testing strategy — for PFactory v0.2.0. The pipeline
that runs IS the showcase that gets recorded. The closure condition is
"triage_report.md has 5 verdicts: 4 accept + 1 reject; evidence files
exist on disk; the showcase page renders correctly with the inline
report and embedded media".

Beyond that:
- The `scripts/seed-aifactory-workspace.sh` helper gets a unit test
  (`tests/test_demo_workspace_seed.sh`) checking idempotence + that the
  `spec.md` it produces parses.
- The `scripts/showcase-portal-recorder.ts` gets a smoke test running
  it against a mocked WebSocket stream.

## Migration / compatibility

None. This is purely additive. No existing PFactory files change apart
from `docs/index.md` (hero CTA append) and `docs/showcase.md` (new
file). Demo repo is brand-new under a separate name; no naming
conflicts. AIFactory workspace simulation uses the documented override
path — no other repos affected.

## Open questions

None — all decisions resolved in the interview.

## Files to be added

| Path | Owner subagent | Purpose |
|---|---|---|
| `olafkfreund/pfactory-demo/**` | A | The whole demo repo |
| `scripts/seed-aifactory-workspace.sh` | B | One-shot helper for AIFactory simulation |
| `scripts/showcase-portal-recorder.ts` | C | Phase-boundary screenshot harness |
| `docs/showcase.md` | D | New Pages section |
| `docs/index.md` (hero CTA append) | D | Cross-link to /showcase/ |

## References

- v0.2.0 release: <https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0>
- Task 16 evidence module: `apps/backend/agents/evidence/`
- Task 16 follow-up (Triager evidence-links): commit `5d8f588`
- Decision 7 (env-var indirection): `.pfactory.yml` stores names not values
- Decision 11 (null vs zero coverage): Browser lane has no line coverage
- Decision 12 (evidence capture): screenshots / video / trace / HAR per test
- Existing fixture for shape reference: `tests/fixtures/planner_smoke/`
- Handover skill (PFactory-side):
  `.claude/skills/handover-to-pfactory/SKILL.md`
- Companion handover (AIFactory-side):
  `companion-skills/aifactory-handover-to-pfactory/SKILL.md`
