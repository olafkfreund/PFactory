---
layout: post
title: "Planning joins the shell: one product, one sign-on, the latest models"
subtitle: "PFactory's planning portal picks up the fleet-wide command palette, needs-you badge, and silent single sign-on — and moves onto Claude Opus 4.8. A one-page showcase is ready to download."
date: 2026-07-07 06:00:00
author: DataSeek Team
---

PFactory turns an idea into a governed, deployment-aware plan, with a human
approval gate in front of every agent. This round of work connected it to the rest
of the family and moved it onto the current model lineup.

![PFactory Planning Portal]({{ '/assets/blog/2026-07-07/planning-portal.png' | relative_url }})

## Part of one product now

The planning portal is no longer an island:

- **A portal switcher** in the top bar moves you between Plan, Build, Test, and
  Cockpit as one product.
- **A global command palette** — Cmd-K — searches every portal's work, not just
  PFactory's, and jumps straight to it.
- **A fleet "needs you" badge** shows how many tasks across the whole factory are
  waiting on a human, right next to the switcher.
- **Silent single sign-on**: arriving from a sibling portal signs you in without a
  second login.

## On the latest models

Planning now runs on the current model lineup — defaulting to **Claude Opus 4.8**,
with **Claude Sonnet 5** and the current OpenAI, Google, and GitHub models
available per stage.

## Still governed, still deployment-aware

None of the guardrails changed: every agent-produced plan still waits for human
approval before it can emit and hand off, plans still read the existing codebase
for brownfield work, and they still carry a real deployment contract that the build
and test stages inherit.

## Download the showcase

**[PFactory — one-page showcase (PDF)]({{ '/assets/pfactory-showcase.pdf' | relative_url }})**

## The path forward

Next: deeper code-aware planning, richer deployment contracts, and an audit trail
that makes every plan traceable from requirement to reviewed pull request.
