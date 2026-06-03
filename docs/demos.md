---
layout: default
title: Demos
permalink: /demos/
nav_order: 5.6
---

# PFactory demos — every lane, end to end

<div class="reveal" markdown="1">

Each demo below is a **real, unedited pipeline run** on the user's Claude
subscription — not a mock. A developer hands PFactory a finished feature; the
five-agent pipeline (**Planner → Gen-Functional → Executor → Evaluator →
Triager**) plans, writes, sandboxes, scores, and triages a test suite, and
emits the verdicts a reviewer would see on the PR.

Every demo is a single composite screencast: the **Claude Code terminal**
(top-left), the **PFactory portal** (top-right), and the **live triage report**
with real verdicts (bottom). Each one shows at least one **passing** test and
one **failing** test caught by a deliberately seeded bug — proof the grader
actually distinguishes good tests from bad.

These seven demos span the full v0.2 lane spine — **browser** (Playwright),
**unit** (pytest + coverage + 3× stability + mutation), **api** (httpx against a
running service), and **polyglot** (Python pytest **and** TypeScript Jest in one
run) — to show PFactory tests **far more than web pages**, and grades them on
real signals: it catches implementation bugs *and* rejects weak tests via
mutation.

</div>

## 1 · Greeting generator — browser lane (Playwright)

<div class="reveal" markdown="1">

**What it tests:** a deployed Vite + React SPA. PFactory writes Playwright
tests that drive the UI against the live
[demo site](https://olafkfreund.github.io/pfactory-demo/) and assert on the
output panel.

**Result:** **4 accept + 1 reject.** AC#5 ("two consecutive Generate clicks
must differ") is rejected — it caught a deliberately seeded memoisation cache
bug.

<img src="{{ '/static/demos/greeting-generator/demo.gif' | relative_url }}?v=3"
     alt="Greeting generator browser-lane demo"
     style="width:100%;border:1px solid #2a2a2a;border-radius:8px" loading="lazy" />

[Download MP4]({{ '/static/demos/greeting-generator/demo.mp4' | relative_url }}?v=3)

</div>

## 2 · Failure → merge / dismiss — the human decision

<div class="reveal" markdown="1">

**What it shows:** the **human-in-the-loop** close-out over a run with real
failures — the reviewer **merges the accepted tests** and **dismisses the
rejected ones**. This is the decision a reviewer makes when PFactory flags
failing tests, not just passing ones.

**Result:** 5 accepted (committed) · 2 rejected (dismissed).

<img src="{{ '/static/demos/failure-flow/demo.gif' | relative_url }}?v=3"
     alt="Failure to merge/dismiss flow demo"
     style="width:100%;border:1px solid #2a2a2a;border-radius:8px" loading="lazy" />

[Download MP4]({{ '/static/demos/failure-flow/demo.mp4' | relative_url }}?v=3)

</div>

## 3 · Pricing helper — unit lane (pytest + mutation)

<div class="reveal" markdown="1">

**What it tests:** a pure Python module (`pricing.py`). This is the lane a
browser demo can't show — the **numeric signals**: coverage delta, **3×
stability** re-runs, and **mutate-and-check** (mutmut). A test only passes if it
*kills the mutant*.

**Result:** **4 accept** — every generated test runs cleanly, is stable across
3 re-runs, and kills its mutation. No browser involved.

<img src="{{ '/static/demos/python-unit/demo.gif' | relative_url }}?v=3"
     alt="Python unit-lane demo"
     style="width:100%;border:1px solid #2a2a2a;border-radius:8px" loading="lazy" />

[Download MP4]({{ '/static/demos/python-unit/demo.mp4' | relative_url }}?v=3)

</div>

## 4 · Message board — fill a form, verify it holds the text

<div class="reveal" markdown="1">

**What it tests:** a form page — type a name + message, click Post, and check
the post list holds exactly what was typed (verbatim text, special characters,
the author name). PFactory writes Playwright tests that fill the form and
assert on the rendered result.

**Result:** **3 accept + 1 flag.** The flagged test ("two posts both remain
visible") caught a seeded state bug where a new post replaces the previous one.

<img src="{{ '/static/demos/form-fill/demo.gif' | relative_url }}?v=3"
     alt="Form-fill browser-lane demo"
     style="width:100%;border:1px solid #2a2a2a;border-radius:8px" loading="lazy" />

[Download MP4]({{ '/static/demos/form-fill/demo.mp4' | relative_url }}?v=3)

</div>

## 5 · KV API gateway — api lane (httpx, **no browser**)

<div class="reveal" markdown="1">

**What it tests:** a running REST service (FastAPI key-value gateway). PFactory
writes **httpx** tests — `import httpx`, read `PFACTORY_TARGET_URL`, assert on
`response.status_code` and `response.json()` — and runs them against the live
service. **Zero Playwright, zero browser.** This is the proof that PFactory
tests APIs, gateways, and service connections, not just web pages.

**Result:** **4 accept + 1 flag.** The four contract tests pass; the flagged
test ("a missing key must return 404") caught a seeded contract bug — the
gateway returns HTTP 200 with a null body instead of 404 — and the Evaluator
flags it for human review as a regression guard once the gateway is fixed.

<img src="{{ '/static/demos/api-gateway/demo.gif' | relative_url }}?v=3"
     alt="API gateway api-lane (httpx) demo"
     style="width:100%;border:1px solid #2a2a2a;border-radius:8px" loading="lazy" />

[Download MP4]({{ '/static/demos/api-gateway/demo.mp4' | relative_url }}?v=3)

</div>

## 6 · Shipping brackets — edge-case / boundary hunting

<div class="reveal" markdown="1">

**What it tests:** a `shipping_cost(weight_g)` tiered calculator. PFactory
writes **parametrised** boundary tests — the exact weights at the edge of each
bracket, where off-by-one bugs hide — and **mutation** confirms each test
actually pins its bracket.

**Result:** **3 accept + 1 reject.** The boundary test at exactly **500 g**
caught a seeded off-by-one (it charged $10 instead of $5); mutation *killed* on
the good tests and *survived* on the buggy boundary — PFactory's evidence the
test found a real defect.

<img src="{{ '/static/demos/edge-case/demo.gif' | relative_url }}?v=3"
     alt="Edge-case / boundary unit-lane demo"
     style="width:100%;border:1px solid #2a2a2a;border-radius:8px" loading="lazy" />

[Download MP4]({{ '/static/demos/edge-case/demo.mp4' | relative_url }}?v=3)

</div>

## 7 · Polyglot — Python pytest **and** TypeScript Jest in one run

<div class="reveal" markdown="1">

**What it tests:** a single project with a Python helper (`tax.py`) **and** a
TypeScript helper (`slugify.ts`). From **one** handoff, PFactory's Planner fans
the spec across two languages and two frameworks — generating **pytest** tests
for Python and **Jest** tests for TypeScript — and runs each in its own
container.

**Result:** **5 accept + 2 reject**, and the two rejects show off *two different*
grading powers in one run:
- **caught a real bug** — the TypeScript `slugify` doesn't fold accented
  characters (`Café` → `caf`, not `cafe`); the Jest `ascii-fold` test rejects it;
- **rejected a weak test** — a passing Python test survived **mutation** (its
  `-1` constant didn't pin the boundary), so PFactory rejected it for
  insufficient assertion quality.

One tool, two languages, real bugs *and* weak-test detection.

<img src="{{ '/static/demos/polyglot/demo.gif' | relative_url }}?v=3"
     alt="Polyglot (Python pytest + TypeScript Jest) demo"
     style="width:100%;border:1px solid #2a2a2a;border-radius:8px" loading="lazy" />

[Download MP4]({{ '/static/demos/polyglot/demo.mp4' | relative_url }}?v=3)

</div>

## How these were produced

<div class="reveal" markdown="1">

All seven were generated by the **`/demo`** command, which drives a scenario
end-to-end and refuses to publish a demo until an automated **quality gate**
passes (multi-pane frame · real pipeline run · a pass *and* a fail in the
report · web-embeddable output). Scenario definitions live in
`tests/fixtures/demo-scenarios/`; the production scripts live in `scripts/demo/`.

</div>
