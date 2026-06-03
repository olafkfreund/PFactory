---
layout: post
title: "Introducing PFactory: the Plan Factory"
subtitle: "The autonomous-coding world collapsed planning into execution. PFactory pulls it back out — and puts a governance gate in front of every agent."
date: 2026-06-03
author: DataSeek Team
---

In 2026 the autonomous software engineers got very good at one thing: hand them a
vague ask and they will plan *and* build it in a single loop. Devin, Factory's
droids, the GitHub Copilot coding agent — you give them an issue, they branch,
code, test, and open a PR. The plan is implicit, internal, and gone the moment the
code lands.

That optimizes for generation speed. But the industry's own stated constraint for
the year is the opposite: **the gap between what agents can generate and what teams
can confidently verify.** The bottleneck isn't typing code anymore — it's trust.

**PFactory is the missing front of the chain.** It's the third factory in the
suite: [AIFactory](https://github.com/olafkfreund/AIFactory) executes tasks,
TFactory tests them, and **PFactory plans them** — as a deliberate, reviewable,
*governed* step before any agent writes a line.

### What it does

1. **Ingest** a project plan — uploaded as `docx` / `pdf` / `markdown`, or via the
   MCP control plane, the CLI, or a GitHub issue.
2. **Enrich** it with *live* context: internal wikis and **Backstage** (catalog,
   TechDocs, golden-path templates) for policy and best-practice, plus *read-only*
   introspection of your running **Kubernetes / OpenShift / Azure / AWS / GCP** —
   real load, quotas, network policies, resources — and **Terraform** and cloud
   best-practice MCP servers. We plan from the prompt **and the running system**.
3. **Decompose** it into an epic and child issues. For software, that includes a
   task breakdown, a generated **Testing Strategy**, and a generated **CI/CD**
   pipeline.
4. **Review** it through mandatory **architecture / security / best-practices /
   feasibility** gates — a hybrid of deterministic policy-as-code (Checkov, OPA,
   cloud-native policy) and LLM lenses — measured against templates that *carry
   their own rules*. Then one human approval.
5. **Emit** governed **GitHub epics + child issues** — the durable, auditable
   source of truth — that AIFactory picks up and executes.

### Why a separate factory

Because the plan should be the reviewed artifact, not a side-effect of execution.
A governed epic with gate scores and a human sign-off is the compliance story the
single-loop agents can't tell. And because everything is pluggable — add MCP
servers, skills, agents, and Backstage-compatible templates through a declarative
registry, and let PFactory keep those templates current by proposing updates *via
pull request* as the clouds change.

Plan. Govern. Hand off. That's the Plan Factory.

> PFactory is bootstrapping in the open — forked from the TFactory skeleton and
> rebranded, with the planning pipeline under construction. Follow along in the
> [build epic](https://github.com/olafkfreund/PFactory/issues/1).
