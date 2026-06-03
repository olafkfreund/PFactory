---
layout: post
title: "Make the plan the reviewed artifact"
subtitle: "When agents plan inside the execution loop, there's nothing to approve until the code already exists. PFactory moves the review forward."
date: 2026-06-02
author: DataSeek Team
---

The autonomous coding agents are fast. The thing slowing teams down isn't
generation — it's **verification**: the gap between what an agent produces and
what a team can confidently sign off on. When planning happens *inside* the
execution loop, the first reviewable artifact is a pull request full of code. By
then the expensive work is done, and "review" means reverse-engineering intent
from a diff.

PFactory inverts that. The **plan** is the reviewed artifact — examined and
approved *before* anything is built.

Every plan passes through four gates:

- **Architecture** — does it fit the system's structure, boundaries, and
  ownership?
- **Security** — does it respect policy, least privilege, and data-handling
  rules? (Blocking findings hard-fail.)
- **Best practices** — does it follow the org's golden paths and template rules?
- **Feasibility** — is it sane and doable against the *real* infrastructure and
  capacity?

These aren't just an LLM's opinion. The gates are a **hybrid**: deterministic
policy-as-code (Checkov, OPA, cloud-native policy via provider MCP servers)
catches the hard, reproducible rules, while LLM lenses catch the judgment calls —
architecture fit, structural risk, feasibility. Scores aggregate against a
threshold.

Only after the gates pass does a **single human approval** unlock issue creation.
And the approval is bound to the plan's content hash — edit the plan, and the
approval is invalidated, so nobody can quietly change scope after sign-off.

What comes out the other side is a GitHub epic and child issues with a full audit
trail: the gate scores, the findings, and who approved what. That trail is the
governance story the single-loop agents can't tell — and it's exactly what
regulated and platform teams need before they let an agent build anything.

Govern the plan, and the velocity downstream is something you can actually trust.
