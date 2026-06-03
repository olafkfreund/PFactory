---
layout: post
title: "One spec, five lanes, three languages"
subtitle: "Most test generators speak one language. Real features don't. Here's how PFactory fans a single handoff across pytest, Jest, and Playwright."
date: 2026-05-31 13:00:00
author: DataSeek Team
---

Here's a thing nobody tells you about "AI writes your tests": most tools only
write *one kind* of test, in *one language*. Great, if your entire product is a
single Python module. Less great if your feature is — like every real feature —
a React form talking to an httpx endpoint backed by a database.

A monorepo team tries that single-language tool, watches it ignore 80% of the
change, and politely closes the tab.

## The five-lane spine

PFactory's v0.2 plans a feature across five lanes:

```
unit · browser · api · integration · mutation
```

The Planner reads your acceptance criteria and the diff, then tags each
sub-task with the lane *and language* it belongs in. A login feature might fan
out to a **pytest** unit test for the token logic, a **Jest** test for the React
hook, a **Playwright** browser test for the actual click-through, and an **api**
test that hits the running endpoint. One handoff, one plan, four lanes lit.

## Why lanes instead of "just write tests"

Because the lanes don't just describe *what* to test — they decide *how to run
and judge it*. A browser test gets a Playwright sandbox and is scored without
expecting line coverage (you're testing a flow, not a function). A unit test
gets pytest + coverage + the full mutation probe. The verdict pipeline applies
the right rubric per lane, then reports them **uniformly** — same five-signal
card whether the test is Python or TypeScript.

That uniformity is the quiet superpower. You don't get five different dashboards
in five different dialects. You get one triage report: here are the good tests
across your whole stack, ranked, with a verdict each.

## Security is deliberately *not* a lane

We cut SAST/DAST/fuzz on purpose. They're real, but they're a different job with
mature dedicated tools — bolting them on would have made PFactory worse at the
thing it's actually good at. Honest scope beats a longer feature list.

## What it feels like

You finish a polyglot feature, hand the branch over, and watch the portal's lane
grid light up one cell at a time as the plan fans out — then collapse back into
a single ranked list of tests worth keeping.

The [demos](/demos/) include a polyglot run if you want to see the fan-out for
real; the [architecture page](/architecture/) has the lane-by-lane detail.
