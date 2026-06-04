---
layout: default
title: Architecture
permalink: /architecture/
nav_order: 7
---

# Architecture

PFactory is the **feasibility & governance gate** between an AI plan and execution.
It takes a plan — including the output of spec-authoring tools (GitHub Spec-Kit,
AWS Kiro, BMAD-METHOD) — grounds it in your **live cloud**, proves it is
*buildable and affordable* before any code (cost · IAM access · quotas), gates it on
architecture / security / best-practice / feasibility lenses, takes one human
approval, and emits governed, execution-ready GitHub issues. Each of the eight
stages is a self-contained unit with a clear input and output, so the plan can be
inspected, reviewed, and audited at every step.

## The pipeline

`Ingest → Enrich/Discover → Detect → Decompose → CI/CD + Testing synthesis →
Feasibility + Review-gates → Human-approval → Emit`

| # | Stage | What it does |
|---|-------|--------------|
| 1 | **Ingest** | Parse an uploaded plan (docx / pdf / markdown) or one delivered via the MCP control plane, the CLI, or a GitHub issue, into a normalized model (`NormalizedPlan`). |
| 2 | **Enrich / Discover** | Pull *live* context: internal wikis &amp; Backstage (catalog, TechDocs, golden-path templates) and *read-only* introspection of running Kubernetes / OpenShift / Azure / AWS / GCP + Terraform — workloads, resource limits, HPA, network policies, quotas, live load. |
| 3 | **Detect** | Classify the deliverable as software vs non-software. Software unlocks the deep path (task breakdown + testing + CI/CD + code gates); other deliverables get review + decomposition. |
| 4 | **Decompose** | Turn the plan into an epic + child issues with dependencies (`EpicPlan`). |
| 5 | **Synthesize** | For software: generate a Testing Strategy and a CI/CD pipeline definition, each as a doc plus a dedicated child issue. |
| 6 | **Feasibility + Review-gates** | **Feasibility:** price the proposed resource shape through the real cloud pricing APIs (AWS Price List · Azure Retail · GCP Catalog, static fallback), run `iam:SimulatePrincipalPolicy` for the actions the plan implies (grant/deny per action), and roll up a calibrated effort band — *before any code exists*. **Gates:** architecture / security / best-practice / feasibility lenses, a hybrid of deterministic policy-as-code (Checkov · OPA · cloud-native policy via MCP) and LLM reviewers, scored against a threshold. An over-budget or access-denied plan routes to a human — it never silently blocks. |
| 7 | **Human-approval** | A single human approval gate. Gates must pass first; any edit to the plan invalidates the approval (content-hash check). |
| 8 | **Emit** | Create the GitHub epic + child issues (the durable source of truth) and hand off to AIFactory — by writing a `requirements.json`, triggering its API, or via a labelled issue. |

## Data contracts

- **`NormalizedPlan`** — the ingested plan: id, title, description, source format
  &amp; channel, acceptance criteria, target kind, plan type, the enrichment
  bundle (infra + knowledge), and a content hash.
- **`PlanReview`** — per-lens scores and findings, the aggregate vs threshold,
  whether code gates applied, and the human-approval record (with hash
  invalidation).
- **AIFactory handoff** — `{ title, description, metadata }` written to
  `.aifactory/specs/{id}/requirements.json` (preferred), POSTed to
  `/api/tasks/create-and-run`, or attached to a labelled GitHub issue.

## Extensibility

A declarative **registry** lets you add MCP servers, skills, agents, and
templates without forking. **Provider MCP servers** (AWS · Azure · GCP ·
Terraform) drive automatic best-practice review. **Templates** are Backstage
Software Template-compatible and carry an embedded `policy:` block — they
scaffold *and* enforce their rules. A drift watcher proposes template updates via
pull request as the clouds change.

## Where it sits

PFactory sits **downstream** of spec-authoring tools (GitHub Spec-Kit, AWS Kiro,
BMAD-METHOD) — it *consumes* their spec rather than competing with them; they stop
at text, PFactory grounds and governs it — and **upstream** of any execution agent.
Within the suite it **plans & governs**,
[AIFactory](https://github.com/olafkfreund/AIFactory) **executes** the emitted
issues, and TFactory **tests** the result — `plan → build → test`, governed end to
end. The emitted GitHub issues are agent-agnostic: AIFactory, the Copilot coding
agent, Devin, or a human can pick them up.

The full design spec lives in
[`docs/plans/2026-06-03-pfactory-design.md`](https://github.com/olafkfreund/PFactory/blob/main/docs/plans/2026-06-03-pfactory-design.md).
