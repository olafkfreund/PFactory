---
layout: default
title: Roadmap
permalink: /roadmap/
---

# Roadmap

PFactory is built by cloning the proven factory skeleton and transforming it into
the planning pipeline. Work is tracked as one epic with child issues on GitHub:
**[Epic #1](https://github.com/olafkfreund/PFactory/issues/1)**.

| Phase | Goal |
|-------|------|
| **P0 — Clone &amp; rebrand** ✅ | Fork the skeleton, rebrand to PFactory, public repo, brand &amp; logo, design + positioning docs, Pages site. *Done.* |
| **P1 — Ingest** | PDF/DOCX loaders, the `NormalizedPlan` model, and all four intake channels (portal upload, CLI, MCP, GitHub). |
| **P2 — Detect &amp; Decompose** | Software vs non-software classifier; the planner emits an `EpicPlan`; plan-type descriptors. |
| **P3 — Enrich** | Read-only live infra adapters — Kubernetes first, then OpenShift / Azure / AWS / GCP — plus knowledge connectors (Git markdown &amp; Backstage first, then Confluence / GitBook / Notion). |
| **P4 — Synthesize** | Generate Testing Strategy and CI/CD pipeline definitions, each with a dedicated child issue. |
| **P5 — Review** | The four review lenses with a scoring threshold; the hybrid deterministic + LLM rules engine; the human-approval gate. |
| **P6 — Emit** | The GitHub epic + child-issue emitter and the AIFactory handoff. |
| **P7 — Ship** | The planning portal UI, Helm chart, Docker images, and MCP server. |
| **P8 — Providers &amp; templates** | Cloud / IaC best-practice MCP servers, the declarative registry, Backstage-compatible templates with embedded policy, and the living-template drift watcher. |

## Sequencing

P0 unblocks everything. P1 (the `NormalizedPlan` spine) feeds P2, P3, and P4. P2's
detection gates the software path. P3 and P4 run in parallel and both feed the P5
review gates — which must pass before P6 emits anything. Governance before
hand-off is the whole point.

Follow the live status on the
**[build epic](https://github.com/olafkfreund/PFactory/issues/1)**.
