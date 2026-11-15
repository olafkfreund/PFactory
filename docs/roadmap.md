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

## Shipped — the planning engine

The full pipeline (`Ingest → Enrich → Detect → Decompose → Synthesize →
Feasibility + Review-gates → Human-approval → Emit`) is delivered:

| Phase | Delivered |
|-------|-----------|
| **P0 — Clone &amp; rebrand** | Public repo, brand, design + positioning docs, Pages site. |
| **P1 — Ingest** | PDF/DOCX/markdown loaders, the `NormalizedPlan` model, all four intake channels (portal upload, CLI, MCP, GitHub) — plus generic AC sources (Gherkin/EARS) and Spec-Kit/Kiro/BMAD output. |
| **P2 — Detect &amp; Decompose** | Software vs non-software classifier; the planner emits an `EpicPlan`; plan-type descriptors. |
| **P3 — Enrich** | Read-only live-infra adapters (Kubernetes / OpenShift / Azure / AWS / GCP + Terraform) + knowledge connectors (Git markdown &amp; Backstage). |
| **P4 — Synthesize** | Testing Strategy and CI/CD pipeline definitions, each with a dedicated child issue. |
| **P5 — Feasibility + Review** | Pre-code feasibility (live cloud cost · `iam:SimulatePrincipalPolicy` access · quotas · effort), the review lenses with a scoring threshold, the hybrid deterministic + LLM rules engine, and the human-approval gate. |
| **P6 — Emit** | The GitHub epic + child-issue emitter with the documented tag taxonomy, and the AIFactory handoff (dry-run by default). |
| **P7 — Ship** | The planning portal UI, Helm chart, Docker images, and MCP server. |
| **P8 — Providers &amp; templates** | Cloud / IaC best-practice MCP servers, the declarative registry, Backstage-compatible templates with embedded policy, and the living-template drift watcher. |

## Recently shipped, August to November 2026 (current: v0.6.42)

Twenty-six releases, [v0.6.17](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.17)
to [v0.6.42](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.42)
(2026-10-31), listed in the [release notes](https://github.com/olafkfreund/PFactory/releases).
The write-up is in
[What shipped from August to November]({{ '/blog/2026/11/15/what-shipped-august-to-november/' | relative_url }}).

- **Several replicas on a shared store** — plan sessions, run leases, task logs
  and WebSocket events live in Postgres
  ([v0.6.20](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.20),
  [v0.6.25](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.25),
  [v0.6.30](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.30),
  [v0.6.31](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.31)).
  The autoscaler's one-replica pin stays in place.
- **Org-scoped access control** — plan session reads and writes are scoped to
  the caller's org for `acw_` API keys; role checks on task routes run in
  shadow mode until `RBAC_ENFORCE_TASK_ROUTES` is set
  ([v0.6.38](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.38),
  [v0.6.41](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.41),
  [v0.6.42](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.42)).
- **Plan review** — seven review lenses, and a plan whose worked example
  contradicts its own invariant is blocked
  ([v0.6.19](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.19),
  [#402](https://github.com/olafkfreund/PFactory/issues/402)).
- **Audit evidence** — run signature v2, a hash-chained audit log that no
  longer forks, and an optional warn-only Ed25519 signature
  ([v0.6.27](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.27),
  [v0.6.23](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.23),
  [v0.6.35](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.35)).
- **Runtime invariants** — on by default; no check is critical yet
  ([v0.6.28](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.28)).

In progress on `dev`, not in a release: WebSocket scoping for `acw_` keys
([#991](https://github.com/olafkfreund/PFactory/pull/991),
[#994](https://github.com/olafkfreund/PFactory/pull/994)), an allowlisted
environment for agent child processes
([#997](https://github.com/olafkfreund/PFactory/pull/997)), and originator and
agent identity on the run signature
([#1000](https://github.com/olafkfreund/PFactory/pull/1000)).

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
