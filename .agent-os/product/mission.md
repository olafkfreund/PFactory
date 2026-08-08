# Product Mission

> Last Updated: 2026-06-04
> Version: 2.0.0

## Pitch

PFactory is the **feasibility & governance gate** between an AI-authored plan and
the execution agents that build it. It ingests a project plan, grounds it in the
customer's **live cloud** (read-only cost, IAM-access and quota checks *before any
code*), runs architecture / security / best-practice / feasibility review gates,
requires one human approval, and emits governed, tagged GitHub epics + child issues
that AIFactory / Copilot / Devin / a human can execute — with a full audit trail.

It sits **downstream** of spec-authoring tools (GitHub Spec-Kit, AWS Kiro,
BMAD-METHOD) and **upstream** of any execution agent. It does not compete with them
on authoring the spec — it does the thing none of them does: prove the plan is
*buildable and affordable in your actual environment*, then gate it.

## Users

### Primary Customers

- **Platform & compliance teams adopting coding agents:** the people accountable
  when an AI plan turns into infrastructure. They need a reviewable, audit-trailed
  gate — cost, access, policy — before work reaches an execution agent.
- **Architects & tech leads at orgs with real cloud estates:** teams whose plans
  touch live AWS / Azure / GCP / Kubernetes and who want "can we actually build
  this, and what will it cost?" answered *before* the build starts, not after.

### User Personas

**Platform / Staff Engineer** (30-50 years old)
- **Role:** Owns golden paths, platform policy, and the bridge to coding agents
- **Context:** The org is adopting autonomous coding agents; leadership wants
  velocity *and* a governance story (often EU-AI-Act-driven)
- **Pain Points:** AI plans look plausible but hide cost, access, and policy
  landmines; nothing gates a plan against the *running* system before code starts
- **Goals:** a cited, auditable verdict on every plan — feasibility + policy + a
  human approval — without becoming the bottleneck the agents were meant to remove

**Architect / Tech Lead** (30-50 years old)
- **Role:** Reviews designs, signs off on what gets built
- **Context:** Receives plans from Spec-Kit/Kiro/BMAD or a team of engineers and
  must decide go / no-go
- **Pain Points:** estimates are guesswork; "will IAM even let us do this?" is
  discovered mid-build; the plan document gets silently rewritten by tools
- **Goals:** a grounded cost/effort/access estimate, cited suggestions attached to
  the *original* document (never overwritten), and governed handoff to execution

## The Problem

### AI plans are approved without proof they're buildable

The 2026 coding agents collapsed planning *into* execution: hand them a vague ask
and they plan and build in one loop. That optimizes generation speed, but the plan
is never the reviewed artifact — humans review *output*, after the cost was
incurred. Spec-authoring tools (Spec-Kit, Kiro, BMAD) stop at *text*: they produce
a well-shaped spec from the prompt, but none checks it against your running cloud.

**Our Solution:** make the *plan* the reviewed, auditable artifact. Ground it in
the live environment (cost via real cloud pricing APIs, technical access via IAM
policy-simulation, quotas), gate it on architecture / security / best-practice /
feasibility lenses, and require a human approval before emit.

### "Velocity without verification" is the real 2026 bottleneck

The constraint isn't how fast agents generate — it's the gap between what they
generate and what teams can confidently approve. Regulation is making that gap a
hard requirement: EU AI Act high-risk obligations land **Aug 2, 2026**.

**Our Solution:** a governed emit + audit trail (epic + issues + cited gate scores
+ human approval) — the compliance story the execution vendors lack, grounded in
the customer's real infrastructure.

## Differentiators

### Live-infra grounding (the hardest asset to copy)

Unlike spec-authoring tools (Spec-Kit, Kiro, BMAD) that plan from the *prompt and
stop at text*, PFactory plans from the prompt **+ the running system**: read-only
introspection of AWS / Azure / GCP / Kubernetes feeding the plan, plus a cost
estimate priced through the real cloud pricing APIs *before any IaC exists*.

### Pre-code feasibility: cost · time · technical access

PFactory answers "can we build this, and what will it cost?" at *plan* time:
real on-demand pricing for the account's actual shape, `iam:SimulatePrincipalPolicy`
for the actions the plan implies (grant/deny per action), and a calibrated effort
rollup with stated assumptions. This is a stage Infracost (which prices *declared*
IaC) structurally can't reach — so PFactory **integrates** Infracost downstream
rather than competing with it.

### Help, never override

PFactory never rewrites the customer's document. It attaches **cited, anchored**
suggestions to the original — *why* each change, with a source link — and offers an
improved draft beside it. Missing credentials, regions, or MCP servers degrade to
advisories, never errors. Side-effects (git, PR, emit) are dry-run by default;
nothing reaches an execution agent without a human approving a plan that already
passed the gates.

### Agent-agnostic, governed emit

Output is standard GitHub epics + child issues carrying a documented tag taxonomy
(`pfactory` marker · `handoff:aifactory`/`handoff:tfactory` · `type:*`/`plan-type:*`
/`priority:*` · machine-readable `pfactory:meta`). Any execution agent — AIFactory,
Copilot coding agent, Devin, or a human — can pick them up. PFactory is the front
of *any* execution stack, not a walled garden.

## Key Features

### Core Features

- **Ingest any plan:** docx / pdf / markdown upload, or the MCP control plane, CLI,
  or a GitHub issue — and consume the output of Spec-Kit / Kiro / BMAD directly.
- **Live-cloud enrichment:** read-only introspection of running Kubernetes /
  OpenShift / Azure / AWS / GCP + Terraform + internal wikis & Backstage, surfaced
  as cited *AI Context*.
- **Feasibility gate (cost · time · access):** real cloud pricing APIs, IAM
  policy-simulation, calibrated effort rollup — an over-budget plan routes to a
  human, it never silently blocks.
- **Review gates:** architecture / security / best-practice / feasibility lenses
  (deterministic policy-as-code + LLM lenses) with cited, deduped, actionable
  findings, plus one mandatory human approval.
- **Governed emit:** tagged GitHub epic + child issues with a `pfactory:meta` block
  (cost, effort, access, citations) — dry-run by default.

### Governance & Workflow Features

- **Honoured-document suggestions:** cited, anchored edits attached to the original
  — accept / reject / adopt an improved draft; the source doc is never overwritten.
- **Categories + Backstage-compatible templates:** each template carries an embedded
  `policy:` block enforced through the gates — opt-in, so a clean plan is never
  penalised for a label it didn't pick.
- **Plans on a board:** Plans ready → In Progress → AI Review → Human Review → Done,
  bucketed by state, with approve / emit from the card.
- **MCP- & API-first handoff:** `mcp__pfactory__plan_*` tools let Claude Code,
  Antigravity, and Codex/Copilot hand a plan in and track it to approval.
- **Honest egress posture (BYO-LLM):** LOCAL / SELF_HOSTED / MANAGED_CLOUD
  classification so regulated teams get a truthful "LOCAL - endpoint is on your network" badge.
