---
layout: post
title: "0.6.0: PFactory joins the catalog, and starts counting its own cost"
subtitle: "PFactory is now a first-class citizen of the Backstage software catalog with TechDocs, and its completion events carry the token usage CFactory needs to attribute spend across the Factory family."
date: 2026-06-05
author: DataSeek Team
---

A governance gate only earns trust if it is itself governed and observable. With
**0.6.0** PFactory takes two steps in that direction: it registers itself in the
platform catalog, and it starts reporting what each plan run actually costs.

### In the catalog, with docs that build

PFactory is now onboarded to the **Backstage software catalog**. That means the
service — and its `pfactory-portal` sub-component — show up where platform teams
already look, with enriched annotations, ownership, and links back to the repo.
**TechDocs** are wired through MkDocs with a *strict* build, and CI validates the
catalog entity on every change, so the catalog never drifts from reality.

This is the same move we ask of the plans PFactory governs: be discoverable, be
documented, be verifiable. It is only fair that the gate hold itself to it.

### Counting the cost — RFC-0001 token usage

[CFactory](https://github.com/olafkfreund/CFactory), the cockpit watching over the
Factory family, has a **Tokens & cost** page that attributes LLM spend per service.
Until now PFactory showed up there as *"not instrumented yet."* No longer.

PFactory's completion event — the normalized envelope it already emits when a plan
run reaches a terminal state — now carries an additive `usage` block:

```json
"usage": {
  "input_tokens": 0,
  "output_tokens": 0,
  "total_tokens": 0,
  "cost_usd": 0.0,
  "model": "claude-..."
}
```

A new zeros-safe accumulator sums the token usage across a Plan run's LLM seams.
The block is **honest**: the planning pipeline is deterministic by default, so when
no model is called the numbers are genuinely zero — and the moment a plan run is
routed through an LLM seam, real tokens and cost surface for CFactory to attribute.
It is additive and optional, so nothing downstream breaks.

This is deliberately the *plumbing* — the envelope and the accumulator — rather
than a behaviour change to the pipeline. Spend becomes visible the instant a live
run uses a model, without faking a single number in the meantime.

### What's in 0.6.0

- **Completion-event token usage** ([#60](https://github.com/olafkfreund/PFactory/issues/60)) — the additive RFC-0001 v1.1 `usage` block, so CFactory can attribute cost to PFactory.
- **Backstage catalog + TechDocs** (#55–#59) — catalog onboarding, the `pfactory-portal` sub-component, strict-build TechDocs, and CI catalog validation.
- **Port move** (#54) — the portal now runs on **3114** (backend) / **3115** (frontend) to keep the Factory suite collision-free locally.

Full notes are in the [changelog](https://github.com/olafkfreund/PFactory/blob/main/CHANGELOG.md).

> **Part of the [Factory family](https://factory.freundcloud.com/)** — PFactory
> plans · [AIFactory](https://aifactory.freundcloud.com/) builds ·
> [TFactory](https://tfactory.freundcloud.com/) verifies · CFactory watches over
> all four.
