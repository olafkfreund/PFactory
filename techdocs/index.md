# PFactory

**The planning & governance incubator that sits in front of autonomous execution.**

PFactory is the **Prepare / Plan + Review** stage of the [Factory](https://factory.freundcloud.com/)
PARR pipeline. It ingests a plan (docx / pdf / markdown, MCP, CLI, or a GitHub issue),
enriches it with your **live** organizational context, decomposes it into
acceptance-criteria-aligned epics + child issues, runs mandatory review gates, records
human approval, and emits **governed GitHub issues** that AIFactory executes.

## What it does

- **Ingest** plans from many sources into a normalized form.
- **Enrich** with live context — Kubernetes/OpenShift, AWS/Azure/GCP, Terraform state,
  Backstage catalogs & golden-path templates, and internal wikis.
- **Decompose** into epics + child issues aligned to acceptance criteria.
- **Review** through four gates — architecture, security, best-practices, feasibility —
  as a hybrid of deterministic policy (Checkov/OPA-Rego) + LLM lenses, each verdict
  **cited** to its evidence.
- **Approve** — a human approval gate unlocks emission; nothing ships ungoverned.
- **Emit** governed GitHub epics + child issues, and optionally trigger AIFactory.

## Why it matters

Most spec-driven tools collapse planning into execution and are blind to your real
infrastructure. PFactory's wedge is **governance + live-context grounding + citations +
human approval** — the front half of an EU-AI-Act-grade audit trail.

## Where it fits

```
PFactory ──governed issues──▶ AIFactory ──▶ TFactory      (observed by CFactory)
 (Plan)
```

See [Architecture](architecture.md), [Dependencies](dependencies.md),
[Decisions](decisions.md) and [API & MCP](api.md).
