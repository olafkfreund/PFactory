---
layout: post
title: "Plan from the running system, not just the prompt"
subtitle: "Most AI planners read your prompt. PFactory also reads your cluster, your cloud, and your catalog — before it writes a single issue."
date: 2026-06-03
author: DataSeek Team
---

Ask any 2026 coding agent to "add a worker to handle the import queue" and it will
happily plan one — against an imaginary system. It doesn't know your namespace is
already at 90% of its CPU quota, that there's a `NetworkPolicy` blocking egress to
the queue, that your org's golden path mandates a specific base image, or that the
service it's about to touch is owned by another team and has an SLO.

A plan made in a vacuum is a plan that fails review later — or worse, ships and
breaks something. **PFactory plans from the prompt *and* the running system.**

Before it decomposes anything, the **Enrich** stage pulls live, read-only context:

- **Backstage** — the software catalog (ownership, APIs, dependencies), TechDocs,
  and golden-path scaffolder templates, so the plan inherits your standards.
- **Internal wikis** — Confluence, GitBook, repo-native ADRs — for the policies
  and decisions that never make it into code.
- **Live infrastructure** — read-only introspection of running
  **Kubernetes / OpenShift / Azure / AWS / GCP** and **Terraform**: workloads,
  resource requests and limits, autoscalers, network policies, quotas, and actual
  load. Nothing is mutated; PFactory only looks.

That context rides along on the normalized plan and feeds both the decomposition
*and* the review gates. So when the feasibility lens asks "is this sane and
doable?", it's answering against your real capacity — not a guess. And when a
template says "GCP projects must pin these org policies," that rule is checked, not
hoped for.

Planning grounded in reality is the difference between a backlog that looks
plausible and one an execution agent can actually build.
