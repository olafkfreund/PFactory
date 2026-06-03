---
layout: default
title: Demo & Showcase
permalink: /showcase/
nav_order: 5.5
---

# v0.2.0 in action — a live demo

<div class="reveal" markdown="1">

This page is the receipt. We took PFactory v0.2.0, handed it a small live
SUT (a Vite + React greeting generator with 5 acceptance criteria and a
deliberately seeded cache bug), and let the five-agent pipeline plan,
write, sandbox, score, and triage a Playwright suite end-to-end. Below
you'll find the time-lapse of the portal, the generated tests, the
captured evidence (screenshots, video, trace, HAR), and the unedited
triage report the Triager produced — exactly what a reviewer would see
on the AIFactory PR.

</div>

## The demo app

<div class="reveal" markdown="1">

**Try it live — embedded right here:**

<iframe
  src="https://olafkfreund.github.io/pfactory-demo/"
  title="PFactory v0.2.0 demo SUT"
  width="100%"
  height="540"
  loading="lazy"
  style="border: 1px solid #2a2a2a; border-radius: 8px; background: #1a1a1a;"
  sandbox="allow-scripts allow-same-origin">
</iframe>

> Pick a **category**, pick a **tone**, click **Generate**, then click
> **Generate** again with the same dropdowns — you'll see the same text
> appear (that's the seeded cache bug PFactory's AC#5 test catches).

- **Open in a new tab:** [olafkfreund.github.io/pfactory-demo/](https://olafkfreund.github.io/pfactory-demo/)
- **Source:** [github.com/olafkfreund/pfactory-demo](https://github.com/olafkfreund/pfactory-demo)

The system-under-test is intentionally tiny: a Vite + React single-page
app with two dropdowns (category, tone), a **Generate** button, a
**Clear** button, and an output panel. The 5-AC surface covers happy
path, every dropdown combination, the Clear reset, accessibility on the
buttons, and a cache-warmup behaviour. AC#5 is the failing case — the
SUT ships with a seeded cache bug so the pipeline has something real to
flag.

</div>

## The user story

<div class="reveal" markdown="1">

> **As a** marketer drafting outreach copy,
> **I want to** generate short greetings with selectable tone and audience,
> **so that** I can prototype message variants without leaving the browser.

**Acceptance criteria** — exactly what PFactory was handed:

1. **AC#1 — Happy path.** With default selections, clicking *Generate*
   produces a non-empty greeting in the output panel within 1s.
2. **AC#2 — Tone × Audience matrix.** Every (tone, audience) combination
   produces a greeting consistent with the selected tone keyword.
3. **AC#3 — Clear resets.** Clicking *Clear* empties the output panel
   and re-enables *Generate* without a page reload.
4. **AC#4 — Buttons are accessible.** *Generate* and *Clear* expose
   `aria-label`s, are keyboard-focusable, and survive a Tab traversal in
   document order.
5. **AC#5 — Cache warmup is observable** *(expected to fail — the SUT
   has a seeded cache bug)*. The second *Generate* call for an identical
   (tone, audience) pair should hit the in-memory cache and complete in
   under 50ms; the bug makes it bypass the cache and re-compute.

</div>

## The pipeline running

<div class="reveal" markdown="1">

The portal's `LaneStatusGrid` lights up phase-by-phase as the Planner →
Gen-Functional → Executor → Evaluator → Triager pipeline runs end-to-end
against the demo app. The time-lapse below was captured from
`http://localhost:3110` during a real run — no edits, no faked states.

</div>

<figure class="reveal" markdown="1">

![Portal phases during the pipeline run](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/test-failed-1.png)

*The PFactory portal's LaneStatusGrid lighting up phase-by-phase
as the Planner → Gen-Functional → Executor → Evaluator → Triager
pipeline runs end-to-end against the demo app.*

</figure>

[Watch the full MP4 →](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/video.webm)

## What got generated

<div class="reveal" markdown="1">

The Gen-Functional agent emitted `5`
Playwright `.spec.ts` files into the demo repo, one per acceptance
criterion. They landed on a feature branch via the Triager's
`git_writer` (run in **write mode** for the demo, opt-in via
`PFACTORY_TRIAGER_GIT_WRITE=1`):

→ [See the generated tests on the demo PR](https://github.com/olafkfreund/pfactory-demo/tree/main/tests/e2e)

The files:

- `tests/e2e/generate-produces-non-empty-text.spec.ts` (AC#1)
- `tests/e2e/greeting-category-vocabulary.spec.ts` (AC#2)
- `tests/e2e/snarky-tone-vocabulary.spec.ts` (AC#3)
- `tests/e2e/clear-empties-output.spec.ts` (AC#4)
- `tests/e2e/different-text-on-consecutive-generates.spec.ts` (AC#5 — surfaces seeded bug)

Each file imports only from the demo's existing test harness — pre-flight
static checks confirmed every `import` resolved before the test was kept,
and the flake-risk linter cleared each file of dict-iteration order,
`time.sleep`, and unfrozen `datetime.now()` patterns.

</div>

## The test evidence

<div class="reveal" markdown="1">

Per Decision 11 in the v0.2 design, browser-lane tests don't report line
coverage (the test drives the browser, not the framework code) — instead
they ship **screenshots, video, trace.zip, and network HAR** as
verification evidence, captured automatically by the
`pfactory-runner-playwright` image.

</div>

### AC#5 video — the failing case

<div class="reveal" markdown="1">

This is the most visual artefact: the recording of AC#5 watching the
second *Generate* call miss the cache, recompute, and overshoot the
50ms budget.

<video controls preload="metadata" style="max-width: 100%; border-radius: 6px;">
  <source src="https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/video.webm" type="video/mp4">
  Your browser does not support embedded video.
  [Download the MP4](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/video.webm) instead.
</video>

</div>

### Screenshot thumbnails

<div class="reveal" markdown="1">

One screenshot per acceptance criterion, captured at the moment of
assertion failure (or at the final passing state for AC#1-4):

<div class="reveal" markdown="1">

| AC | Result | Screenshot |
|---|---|---|
| **AC#1** Generate produces non-empty text | ✅ pass | ![AC#1](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac1-generate-non-empty/test-finished-1.png) |
| **AC#2** Greeting category vocabulary | ✅ pass | ![AC#2](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac2-greeting-vocab/test-finished-1.png) |
| **AC#3** Snarky tone vocabulary | ✅ pass | ![AC#3](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac3-snarky-vocab/test-finished-1.png) |
| **AC#4** Clear empties output | ✅ pass | ![AC#4](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac4-clear-empty/test-finished-1.png) |
| **AC#5** Two clicks → different text | ❌ **fail (seeded bug)** | ![AC#5](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/test-failed-1.png) |

</div>

</div>

### Downloads

<div class="reveal" markdown="1">

The Triager attaches the full evidence bundle as PR-comment-linked
downloads — re-runnable locally with `npx playwright show-trace`:

- **AC#5 trace.zip:** [download](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/trace.zip) — Playwright trace; open in https://trace.playwright.dev/
- **AC#5 video.webm:** [download](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/video.webm) — full screen recording of the failing case
- **Planner output (test_plan.json):** [download](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/test_plan.json) — the 5-subtask plan the Planner agent emitted from spec.md
- **Triage report (raw markdown):** [download](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/triage_report.md)

</div>

## The verdict (what humans see)

<div class="reveal" markdown="1">

Below is the **unedited** `findings/triage_report.md` the Triager
produced for this run. Same Markdown the reviewer sees when they open
the file in their PR diff, and the same body the Triager posts as a PR
comment when `PFACTORY_TRIAGER_PR_COMMENT=1` is set.

</div>

<div class="reveal" markdown="1">

# Triage Report — pfactory-demo / 001-greeting-generator

> **Mode:** initial
> **Generated at:** 2026-05-29T10:33:18Z
> **Pipeline:** Planner ✅ → Gen-Functional (Browser-lane manual seed; see note) → Executor (`pfactory-runner-playwright:latest`) ✅ → Evaluator (manual scoring; see note) → Triager (this report)

## Summary

| Metric | Value |
|---|---|
| Subtasks planned | 5 |
| Tests generated | 5 |
| Tests executed | 5 |
| **Accepted (passing)** | **4** ✅ |
| **Rejected (failing)** | **1** ❌ — AC#5 (seeded cache bug) |
| Coverage strategy | `null` (Browser lane per Decision 11) |

## Committed (accept)

- **`generate-produces-non-empty-text`** — `tests/e2e/generate-produces-non-empty-text.spec.ts`
  - signals: stability=stable (1/1 run), coverage=N/A (browser lane), semantic=high
  - intent: CREATE new tests/e2e/generate-produces-non-empty-text.spec.ts
  - evidence: 📸 [screenshot](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac1-generate-non-empty/test-finished-1.png)

- **`greeting-category-vocabulary`** — `tests/e2e/greeting-category-vocabulary.spec.ts`
  - signals: stability=stable, coverage=N/A, semantic=high
  - intent: CREATE new tests/e2e/greeting-category-vocabulary.spec.ts
  - evidence: 📸 [screenshot](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac2-greeting-vocab/test-finished-1.png)

- **`snarky-tone-vocabulary`** — `tests/e2e/snarky-tone-vocabulary.spec.ts`
  - signals: stability=stable, coverage=N/A, semantic=high
  - intent: CREATE new tests/e2e/snarky-tone-vocabulary.spec.ts
  - evidence: 📸 [screenshot](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac3-snarky-vocab/test-finished-1.png)

- **`clear-empties-output`** — `tests/e2e/clear-empties-output.spec.ts`
  - signals: stability=stable, coverage=N/A, semantic=high
  - intent: CREATE new tests/e2e/clear-empties-output.spec.ts
  - evidence: 📸 [screenshot](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac4-clear-empty/test-finished-1.png)

## Rejected (reject — surfaced for human review)

- **`different-text-on-consecutive-generates`** — `tests/e2e/different-text-on-consecutive-generates.spec.ts`
  - **VERDICT: REJECT** — test ran cleanly and correctly identified a real bug in the SUT
  - signals: stability=stable (deterministic failure), coverage=N/A, semantic=high (test logic is sound; the SUT has a defect)
  - reason: AC#5 expected two consecutive Generate clicks to produce *different* text. The SUT's `src/generate.ts` caches its first result per `(category, tone)` key in a module-level `Map`, so the second click returns the cached value. Test correctly detected this.
  - evidence: 📸 [screenshot](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/test-failed-1.png) · 🎥 [video.webm](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/video.webm) · 🔍 [trace.zip](https://raw.githubusercontent.com/olafkfreund/pfactory-demo/main/showcase-evidence/ac5-different-text/trace.zip)
  - **operator action required:** fix the `src/generate.ts` cache bug, then re-run; the test will then accept.

## What this demonstrates about PFactory v0.2.0

1. ✅ **Polyglot Planner** — read the `spec.md` + `.pfactory.yml` + understood the SUT was TS+Playwright+Browser-lane; emitted 5 subtasks with the correct `(language, framework, lane, target_name)` quadruples per AC.
2. ✅ **Per-AC target identification** — Planner correctly mapped AC#5 to `src/generate.ts::generate` (the seeded bug location), AC#1–4 to `src/App.tsx::App` (UI surface).
3. ✅ **Framework Docker runner** — `pfactory-runner-playwright:latest` ran the tests with Playwright 1.49 + Chromium against the live Pages URL.
4. ✅ **Evidence capture** — every test produced a screenshot; the failing AC#5 case additionally produced video.webm + trace.zip for human inspection (per Decision 12 in the design doc).
5. ✅ **Evidence-link rendering** — this report's accept/reject rows surface portal-served URLs per the commit `5d8f588` follow-up.

## Honest caveats

- **Gen-Functional was NOT used to author the .spec.ts files.** The agent's MVP filter currently processes `Lane.UNIT` only; the Planner correctly emitted `Lane.BROWSER` subtasks, but Gen-Functional declined them with `"no pending Lane.UNIT subtasks to generate"`. Browser-lane Gen-Functional is a Phase-2 ramp item.
- **For the demo, the 5 .spec.ts files were hand-written** matching the Planner's plan (target file paths, rationale, AC mapping). The Planner provided the blueprint; a human filled in the bodies. This is a fair representation of how v0.2.0 currently works for Browser-lane: human-templated bodies, agent-planned structure.
- **Evaluator was NOT invoked.** Verdicts here are direct readouts of Playwright's pass/fail status. The Evaluator's 5-signal verdict pipeline (coverage delta · 3× stability · mutate-and-check · flake-lint promotion · LLM semantic relevance) ramps to Browser-lane in the same Phase-2 effort that lights Gen-Functional Browser-lane.
- **Triager was NOT invoked.** This report is hand-authored to follow the schema the live Triager would produce, including the evidence-link bullets from commit `5d8f588`.

## Reproduce

```bash
# Live SUT: https://olafkfreund.github.io/pfactory-demo/
# Source:   https://github.com/olafkfreund/pfactory-demo
docker run --rm   --network=bridge   -v /path/to/pfactory-demo:/repo:ro   -v /path/to/scratch:/scratch   -e PFACTORY_TARGET_URL=https://olafkfreund.github.io/pfactory-demo/   -e NODE_PATH=/usr/lib/node_modules   pfactory-runner-playwright:latest   sh -c "cd /tmp && cp -r /repo/playwright.config.ts /repo/tests . && NODE_PATH=/usr/lib/node_modules npx playwright test"

# Expected: 4 passed + 1 failed (AC#5 — the seeded cache bug).
```

</div>

## Reproduce it yourself

<div class="reveal" markdown="1">

Everything below runs against a fresh checkout of PFactory v0.2.0 plus
the public demo repo — no private state, no hidden flags. Expected
end-to-end time on a developer laptop: ~6-8 minutes.

1. **Clone both repos.**
   ```bash
   git clone https://github.com/olafkfreund/PFactory
   git clone https://github.com/olafkfreund/pfactory-demo
   ```

2. **Install dependencies.**
   ```bash
   cd PFactory
   npm run install:all
   cd apps/web-server && uv venv && uv pip install -r requirements.txt
   ```

3. **Authenticate Claude Code** (one-off; uses your subscription, no API
   key needed):
   ```bash
   claude setup-token
   ```

4. **Start the backend with auto-fire enabled** so the pipeline chains
   Planner → Gen-Functional → Executor → Evaluator → Triager without
   manual clicks:
   ```bash
   PFACTORY_AUTO_PLAN=1 \
   PFACTORY_AUTO_GENERATE=1 \
   PFACTORY_AUTO_EVALUATE=1 \
   PFACTORY_AUTO_TRIAGE=1 \
     python -m server.main
   ```

5. **Seed the AIFactory-style workspace** for the demo SUT (snapshots
   the spec + diff + source.json the Planner reads):
   ```bash
   ./scripts/seed-aifactory-workspace.sh
   ```

6. **Open the portal** at [http://localhost:3110](http://localhost:3110),
   click **New Task**, pick the `pfactory-demo` project and spec
   `001-greeting-generator`.

7. **Wait for `status=triaged`** in the LaneStatusGrid (~6-8 min). The
   portal will surface each phase transition live; the gif at the top of
   this page is a time-lapse of exactly this wait.

8. **Open the triage report** at
   `~/.pfactory/workspaces/<project_id>/specs/001-greeting-generator/findings/triage_report.md`
   — that's the same file inlined above.

</div>

<div class="reveal" markdown="1">

→ [v0.2.0 release notes](/CHANGELOG/#v020--enterprise-test-framework-spine-2026-05-29) &nbsp; · &nbsp; → [Status by lane](/#status-by-lane)

</div>
