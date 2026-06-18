---
layout: default
title: Positioning
permalink: /market-positioning/
---

# PFactory — Market Positioning

> Living document (issue-tracked). Sources at the bottom.

## The one-line position

**PFactory is the feasibility and governance gate between an AI plan and its
execution.** It sits downstream of spec-authoring tools (GitHub Spec-Kit, AWS
Kiro, BMAD-METHOD) and upstream of any execution agent (AIFactory, Copilot coding
agent, Devin). It takes a plan, grounds it in your live cloud (cost, IAM access,
quotas — before any code), runs review gates, requires a human approval, and emits
governed, tagged GitHub epics and child issues with a full audit trail.

But the one-line position undersells the real story, which is that PFactory is the
first node of a governed line — so this page covers both: how PFactory differs on
its own, and why PFactory, AIFactory and TFactory together are, in our view, a
better way to ship software with agents than the alternatives.

## What the market looks like in 2026, and where PFactory fits

The space around PFactory is crowded and, in the authoring layer, mostly free. We
do not pretend otherwise. The honest map:

### Upstream — spec authoring (we consume these, not compete)

- **GitHub Spec-Kit** — open-source, the de-facto standard, free, agent-agnostic;
  drives Specify, Plan, Tasks across many agents.
- **AWS Kiro** — agentic IDE: Requirements, Design, Tasks (EARS), AWS-integrated.
- **BMAD-METHOD** — open-source agentic planning that forces a PRD plus
  architecture validation before code, for nothing.

These author a well-shaped spec from the prompt, and stop at text. None checks the
spec against your running cloud. Every Spec-Kit / Kiro / BMAD user is a lead, not a
rival — they have a spec and no way to prove it is buildable. PFactory is the step
after the step they already do for free.

### Downstream / adjacent — we integrate, not reinvent

- **Infracost** — the FinOps standard, but it prices declared IaC (Terraform/HCL),
  which does not exist yet at plan time. PFactory estimates a coarse cost from plan
  text plus live cloud APIs before any IaC; we integrate Infracost once the IaC
  exists rather than competing on IaC accuracy.
- **Port / Cortex / Backstage** own golden paths, catalogs and scorecards;
  **OPA / Conftest / Checkov** own deterministic policy-as-code. PFactory composes
  these for AI plans — orchestrate, do not replace.

### The execution agents (the layer in front of which we sit)

- **Cognition / Devin** — full autonomy; plans and builds in one loop, humans
  review the output. Bundling "governance" into execution and channel.
- **GitHub Copilot coding agent** — assign an issue, get a branch, code, a PR.
  Planning is implicit.

### The gap PFactory fills

Across the execution agents, planning is collapsed into execution and the plan is
never the reviewed artifact. Across the authoring tools, the spec is never grounded
in the running system. The unfilled slot is a grounded, audit-trailed feasibility
and governance gate between the free spec and the execution agent. That is
PFactory — and EU AI Act high-risk obligations landing Aug 2, 2026 make the
audit-trailed gate a requirement, not a nicety.

## PFactory's wedge — how we differ

1. **Live-infra grounding (the hard-to-copy asset).** Plans are enriched with
   read-only introspection of running Kubernetes / OpenShift / Azure / AWS / GCP,
   plus Terraform and Backstage/wikis. Spec-Kit/Kiro/BMAD plan from the prompt;
   PFactory plans from the prompt and the running system.
2. **Pre-code feasibility: cost, time, access.** Real cloud pricing for the
   account's actual shape, IAM policy simulation (grant/deny per action), a
   calibrated effort rollup, and a read-only local-cluster build/run probe — all
   before any code. A stage Infracost structurally cannot reach.
3. **Governance-first, with an audit trail.** Mandatory architecture / security /
   best-practice / feasibility gates plus one human approval before any issue is
   created. A hard readiness gate blocks emission until every check passes (or a
   human records a waiver bound to the plan's content hash). Epic + issues + cited
   gate scores + approval is the compliance story the execution vendors lack.
4. **Help, never override.** Cited, anchored suggestions on the original document
   (never overwritten); missing creds/regions/MCP degrade to advisories;
   side-effects are dry-run by default.
5. **Execution-agent-agnostic output.** Standard tagged GitHub epics and child
   issues — consumable by AIFactory, Copilot, Devin, or humans. The front of any
   execution stack, not a walled garden.

## PFactory + AIFactory + TFactory: the governed line

PFactory is most valuable as the first node of the **PARR line** — Plan, Author,
Run, Review — where each stage is a separate, governed product and a single
**correlation key** (the GitHub issue number) threads the whole story:

```
PFactory  -->  AIFactory  -->  TFactory  -->  CFactory
  plan          build          verify         watch
```

- **PFactory plans and governs.** It turns a request into a decomposed plan,
  grounds it in live infrastructure, gates it, takes one human approval, and emits
  a **signed Task Contract** plus governed issues.
- **AIFactory builds.** It picks up the governed work, executes on whatever model
  the contract names (Claude, Gemini/Antigravity, Codex, Copilot, a local model),
  and hands the built branch — with the deployed URL — to the next stage.
- **TFactory verifies, with evidence you can see.** It tests the *declared*
  acceptance criteria against the *real* deployment, including authenticated and
  2FA-gated UIs, and grades each criterion against a test that actually ran —
  reporting an honest "verified X/Y" with screenshots and recordings as proof.
- **CFactory watches.** A single completion-event schema (RFC-0001, delivered
  at-least-once) lets one cockpit observe the whole line, with the same evidence on
  the finished task.

The value of the suite is not four tools — it is **one governed path from an idea
to a verified change**, where the contract that was approved is provably the
contract that was built and tested, and a human is in the loop only at the gates
that matter.

## Why this is better — our subjective view

This section is opinion, stated plainly.

- **The plan should be the reviewed artifact, not a side effect of a black box.**
  Devin and the coding agents fold planning into execution, so the thing you
  approve is the *output*, after the cost is already sunk. We believe the cheaper,
  safer place to catch "this is the wrong thing, or it can't be built here" is the
  plan — grounded in your real cloud — before a line is written. Approving a plan
  is faster and less risky than reviewing a finished PR you have to unwind.
- **Governance belongs to the platform, not the vendor.** When a single autonomous
  vendor owns plan, build, and "governance," the audit trail is theirs and the
  controls are whatever they ship. PFactory keeps governance as an open, inspectable
  gate (named checks, policy-as-code, cited LLM lenses, hash-bound waivers and
  approvals) and emits standard GitHub artifacts — so your compliance story does not
  depend on trusting one black box.
- **"Verified" should mean evidence, not a green checkmark.** Most pipelines stop at
  "the tests passed." We think a verification claim is only as good as the proof a
  human can see — which is why TFactory captures the rendered page and a recording
  of the test driving it, and grades each acceptance criterion individually. A
  suite that can show you the click is worth more than one that asserts success.
- **Self-hosted and model-agnostic beats a managed walled garden.** The whole line
  runs on your infrastructure and on whatever LLM you already pay for — a flat-rate
  subscription, a self-hosted model, or fully air-gapped — with an honest
  data-egress posture. For regulated and data-sensitive teams that is not a
  preference, it is a requirement the managed autonomous vendors cannot meet.
- **Honesty over polish.** Missing access degrades to an advisory rather than a
  fabricated result; un-automatable steps (a hardware key, a push approval) are
  refused, not faked; what could not be verified is reported as not verified. We
  would rather be trusted than impressive, because in a governance product the
  moment you lose credibility it is gone.

We are not claiming the suite is the only way, or that the authoring tools and
execution agents are not excellent at what they do — they are, and we build on
them. The claim is narrower and, we think, correct: for an organisation that has to
*stand behind* what its agents ship, a governed plan-to-verified-change line, run on
its own infrastructure with visible evidence, is a better foundation than either a
free spec that stops at text or an autonomous agent whose plan you never get to
review.

## Positioning statement

> For engineering orgs adopting autonomous coding agents, PFactory is the
> feasibility and governance gate that takes a plan — including the output of
> Spec-Kit / Kiro / BMAD — grounds it in the org's live cloud (cost, access,
> quotas), gates it on architecture / security / best-practice / feasibility, and
> emits governed, audit-trailed work items any execution agent can build. With
> AIFactory and TFactory it becomes a complete governed line: a plan you can
> approve, a build on the model you choose, and a verification backed by evidence
> you can see — all on your own infrastructure.

## Go-to-market angles

- **Regulated / enterprise (sharpest wedge)** — the audit trail (epic + issues +
  gate scores + human approval) plus live-infra feasibility is the compliance story
  autonomous-coding vendors lack; EU AI Act high-risk obligations land Aug 2, 2026.
- **Platform / Backstage teams** — PFactory operationalizes golden paths: plans must
  pass the org's templates and policies before work is created.
- **Existing Spec-Kit / Kiro / BMAD users** — they have a free spec and no way to
  prove it is buildable; PFactory grounds and governs it.
- **Existing AIFactory / TFactory users** — PFactory completes the line: plan,
  build, verified-with-evidence.

## Risks / watch-items

- **The authoring layer is free and owns the entry point.** Spec-Kit, Kiro, BMAD are
  free. We must be unmistakably the next step, not a paid alternative — lead with
  feasibility and audit, never with "planning."
- **Execution vendors and SIs are bundling governance into execution.** Our
  defensibility is live-infra grounding, pre-code IAM/cost, and an agent-agnostic
  audit trail — integration-heavy and org-specific.
- **Adoption gravity is real** — most platform/governance initiatives fail on
  adoption. Keep the human-approval gate lightweight enough not to become the
  bottleneck the category is trying to remove.
- **Validation risk** — pricing and demand are unproven. The build is real; "will
  anyone pay" is open.

## Sources

- https://github.com/github/spec-kit
- https://github.com/bmad-code-org/BMAD-METHOD
- https://github.com/infracost/infracost
- EU AI Act high-risk obligations (Aug 2, 2026): https://artificialintelligenceact.eu/
- https://siliconangle.com/2026/04/23/cognition-creator-ai-software-engineer-devin-talks-raise-hundreds-millions-25b-valuation/
- https://news.cognizant.com/2026-01-28-Cognizant-and-Cognition-Partner-to-Scale-Autonomous-Software-Engineering-and-Deliver-Business-Value-Across-Enterprise-Operations
