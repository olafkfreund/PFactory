---
layout: default
title: Roadmap
permalink: /roadmap/
---

# Roadmap

PFactory is the **feasibility & governance gate** between an AI plan and execution
(see [Positioning]({{ '/market-positioning/' | relative_url }})). The planning
engine is **shipped and live** on `main` (backend + portal, fully tested). Work is
tracked as one epic with child issues on GitHub:
**[Epic #1](https://github.com/olafkfreund/PFactory/issues/1)**.

## Shipped — the planning engine ✅

The full pipeline (`Ingest → Enrich → Detect → Decompose → Synthesize →
Feasibility + Review-gates → Human-approval → Emit`) is delivered:

| Phase | Delivered |
|-------|-----------|
| **P0 — Clone &amp; rebrand** ✅ | Public repo, brand, design + positioning docs, Pages site. |
| **P1 — Ingest** ✅ | PDF/DOCX/markdown loaders, the `NormalizedPlan` model, all four intake channels (portal upload, CLI, MCP, GitHub) — plus generic AC sources (Gherkin/EARS) and Spec-Kit/Kiro/BMAD output. |
| **P2 — Detect &amp; Decompose** ✅ | Software vs non-software classifier; the planner emits an `EpicPlan`; plan-type descriptors. |
| **P3 — Enrich** ✅ | Read-only live-infra adapters (Kubernetes / OpenShift / Azure / AWS / GCP + Terraform) + knowledge connectors (Git markdown &amp; Backstage). |
| **P4 — Synthesize** ✅ | Testing Strategy and CI/CD pipeline definitions, each with a dedicated child issue. |
| **P5 — Feasibility + Review** ✅ | Pre-code feasibility (live cloud cost · `iam:SimulatePrincipalPolicy` access · quotas · effort), the four review lenses with a scoring threshold, the hybrid deterministic + LLM rules engine, and the human-approval gate. |
| **P6 — Emit** ✅ | The GitHub epic + child-issue emitter with the documented tag taxonomy, and the AIFactory handoff (dry-run by default). |
| **P7 — Ship** ✅ | The planning portal UI, Helm chart, Docker images, and MCP server. |
| **P8 — Providers &amp; templates** ✅ | Cloud / IaC best-practice MCP servers, the declarative registry, Backstage-compatible templates with embedded policy, and the living-template drift watcher. |

## Recently shipped — 0.6.0 (2026-06-05)

Hardening the gate's own platform footprint and observability
([changelog](https://github.com/olafkfreund/PFactory/blob/main/CHANGELOG.md)):

- **Observability** — completion events now carry the additive RFC-0001 `usage`
  block (tokens · cost · model), so [CFactory](https://github.com/olafkfreund/CFactory)'s
  *Tokens & cost* page attributes spend to PFactory
  ([#60](https://github.com/olafkfreund/PFactory/issues/60)).
- **Backstage** — PFactory is onboarded to the software catalog with a
  `pfactory-portal` sub-component, strict-build TechDocs, and CI catalog
  validation (#55–#59).
- **Ports** — portal moved to 3114 (backend) / 3115 (frontend) (#54).

## What's next

With the engine shipped, the focus shifts from *building* to *proving and
sharpening*:

| Horizon | Focus |
|---------|-------|
| **Now — validate** | Land 3–5 design partners and pressure-test the model with real platform/compliance buyers. The build is real; demand is what we're proving next. |
| **Next — deepen the moat** | An EU-AI-Act audit-pack export (epic + issues + gate scores + approval as a compliance artifact), Infracost integration *downstream* (once IaC exists), and deeper policy-as-code composition (OPA · Conftest · Checkov). |
| **Later — focus the suite** | Run the sharpest product best-in-class and integrate with the developer-platform layer (Port · Cortex · Backstage scorecards) rather than re-implementing it. |

## Sequencing (how it was built)

P0 unblocked everything. P1 (the `NormalizedPlan` spine) fed P2, P3, and P4. P2's
detection gates the software path. P3 and P4 ran in parallel and both feed the P5
feasibility + review gates — which must pass before P6 emits anything. Governance
before hand-off is the whole point.

Follow the live status on the
**[build epic](https://github.com/olafkfreund/PFactory/issues/1)**.
