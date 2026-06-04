---
layout: default
title: Positioning
permalink: /market-positioning/
---

# PFactory — Market Positioning

> Created: 2026-06-03 · Living document (issue-tracked). Sources at the bottom.

## The one-line position

**PFactory is the feasibility & governance gate between an AI plan and execution.**
It sits *downstream* of spec-authoring tools (GitHub Spec-Kit, AWS Kiro,
BMAD-METHOD) and *upstream* of any execution agent (AIFactory, Copilot coding
agent, Devin). It takes a plan, grounds it in your **live cloud** (cost · IAM
access · quotas, *before any code*), runs review gates, requires a human approval,
and emits governed, tagged GitHub epics & child issues with a full audit trail.

## What the market looks like in 2026 — and where PFactory fits

The space around PFactory is crowded and, in the authoring layer, mostly **free**.
We don't pretend otherwise. Here's the honest map:

### Upstream — spec authoring (we *consume* these, not compete)

- **GitHub Spec-Kit** — open-source (MIT), ~93k★, drives *Specify → Plan → Tasks*
  across 30+ agents. The de-facto standard, free, agent-agnostic.
- **AWS Kiro** — agentic IDE: *Requirements → Design → Tasks* (EARS), AWS-integrated.
- **BMAD-METHOD** — open-source agentic planning (Analyst/PM/Architect/PO/QA) that
  forces PRD + architecture validation before code, for $0.

These author a well-shaped spec **from the prompt, and stop at text**. None checks
the spec against your running cloud. **Every Spec-Kit / Kiro / BMAD user is a lead,
not a rival** — they have a spec and no way to prove it's buildable. PFactory is the
step *after* the step they already do for free.

### Downstream / adjacent — we *integrate*, not reinvent

- **Infracost** — the FinOps standard, but it prices **declared IaC (Terraform/HCL)**
  — resources that don't exist yet at *plan* time. PFactory estimates a coarse cost
  from plan text + live cloud APIs *before any IaC*; we integrate Infracost once the
  IaC exists rather than competing on IaC accuracy.
- **Port / Cortex / Backstage** — internal developer platforms own golden paths,
  catalogs and scorecards; **OPA / Conftest / Checkov** own deterministic
  policy-as-code. PFactory *composes* these for AI plans — orchestrate, don't replace.

### The execution agents (the layer in front of which we sit)

- **Cognition / Devin** (~$25B raise talks) — full autonomy; plans *and* builds in
  one loop, humans review output. Going enterprise via SIs (Cognizant, Infosys),
  bundling "governance" into *execution + channel*.
- **GitHub Copilot coding agent / Factory droids** — assign an issue → branch, code,
  PR. Planning is implicit.

### The gap PFactory fills

Across the execution agents, **planning is collapsed into execution** and the plan
is never the reviewed artifact. Across the authoring tools, the spec is **never
grounded in the running system**. The industry's own stated 2026 constraint is the
gap between what agents generate and what teams can confidently approve — and EU AI
Act high-risk obligations land **Aug 2, 2026**. The unfilled slot is a **grounded,
audit-trailed feasibility + governance gate** between the free spec and the execution
agent. That is PFactory.

## PFactory's wedge

1. **Live-infra grounding (the hard-to-copy asset).** Plans are enriched with
   *read-only* introspection of running **Kubernetes / OpenShift / Azure / AWS /
   GCP** + **Terraform** + Backstage/wikis. Spec-Kit/Kiro/BMAD plan from the prompt;
   PFactory plans from the prompt **+ the running system**.
2. **Pre-code feasibility: cost · time · access.** Real cloud pricing APIs for the
   account's actual shape, `iam:SimulatePrincipalPolicy` (grant/deny per action),
   and a calibrated effort rollup — *before* any code. A stage Infracost structurally
   can't reach.
3. **Governance-first + audit trail.** Mandatory architecture / security /
   best-practice / feasibility gates + one human approval before any issue is
   created — epic + issues + cited gate scores + approval is the compliance story the
   execution vendors lack (EU-AI-Act-ready).
4. **Help, never override.** Cited, anchored suggestions on the *original* document
   (never overwritten); missing creds/regions/MCP degrade to advisories; side-effects
   dry-run by default.
5. **Execution-agent-agnostic output.** Standard tagged GitHub epics + child issues —
   consumable by AIFactory, Copilot, Devin, or humans. The front of *any* execution
   stack, not a walled garden.

## Positioning statement

> For engineering orgs adopting autonomous coding agents, PFactory is the
> feasibility & governance gate that takes a plan — including the output of
> Spec-Kit / Kiro / BMAD — grounds it in the org's live cloud (cost · access ·
> quotas), gates it on architecture / security / best-practice / feasibility, and
> emits governed, audit-trailed work items any execution agent can build. Unlike
> the authoring tools, which stop at text, and unlike Devin/Copilot, which plan
> inside the execution loop, PFactory makes the *plan* the reviewed, auditable
> artifact — proven buildable before a line is written.

## Go-to-market angles

- **Regulated / enterprise (sharpest wedge)** — the audit trail (epic + issues +
  gate scores + human approval) + live-infra feasibility is the compliance story
  autonomous-coding vendors lack; EU AI Act high-risk obligations land Aug 2, 2026.
- **Platform/Backstage teams** — PFactory operationalizes golden paths: plans must
  pass the org's templates & policies before work is created.
- **Existing Spec-Kit / Kiro / BMAD users** — they have a free spec and no way to
  prove it's buildable; PFactory grounds and governs it.
- **Existing AIFactory/TFactory users** — completes plan → build → test.

## Risks / watch-items

- **The authoring layer is free and owns the entry point.** Spec-Kit (93k★),
  Kiro, BMAD are $0. We must be unmistakably the *next* step, not a paid
  alternative — or we read as "a worse paid Spec-Kit." Lead with feasibility +
  audit, never with "planning."
- **Execution vendors and SIs are bundling governance into execution** (Devin +
  Cognizant/Infosys). Defensibility = **live-infra grounding + pre-code IAM/cost +
  agent-agnostic audit trail**, which are integration-heavy and org-specific.
- **Adoption gravity is real** — ~70% of platform/governance initiatives fail on
  adoption. Keep the human-approval gate lightweight enough not to become the
  bottleneck the category is trying to remove.
- **Validation risk** — pricing and demand are unproven (zero design partners). The
  build is real; "will anyone pay" is open. See `pricing.md`.

## Sources

- https://medium.com/@dave-patten/the-state-of-ai-coding-agents-2026-from-pair-programming-to-autonomous-ai-teams-b11f2b39232a
- https://techcommunity.microsoft.com/blog/appsonazureblog/an-ai-led-sdlc-building-an-end-to-end-agentic-software-development-lifecycle-wit/4491896
- https://www.cio.com/article/4134741/how-agentic-ai-will-reshape-engineering-workflows-in-2026.html
- https://www.coderabbit.ai/guides/agentic-sdlc
- https://www.sonarsource.com/resources/library/what-is-agentic-sdlc/
- https://www.tembo.io/blog/devin-alternatives-2025
- https://siliconangle.com/2026/04/23/cognition-creator-ai-software-engineer-devin-talks-raise-hundreds-millions-25b-valuation/
- https://news.cognizant.com/2026-01-28-Cognizant-and-Cognition-Partner-to-Scale-Autonomous-Software-Engineering-and-Deliver-Business-Value-Across-Enterprise-Operations
- https://github.com/github/spec-kit
- https://github.com/bmad-code-org/BMAD-METHOD
- https://github.com/infracost/infracost
- EU AI Act high-risk obligations (Aug 2, 2026): https://artificialintelligenceact.eu/
