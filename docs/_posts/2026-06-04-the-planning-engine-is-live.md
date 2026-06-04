---
layout: post
title: "The planning engine is live: feasibility you can trust, emit you can govern"
subtitle: "PFactory now takes a plan end-to-end — enrich, price it against your real cloud, review it with cited findings, honour the document, and emit governed GitHub issues. Here's everything that shipped, and a demo."
date: 2026-06-04
author: DataSeek Team
---

When we [introduced PFactory]({{ '/blog/2026/06/03/introducing-pfactory-the-plan-factory/' | relative_url }})
the pitch was simple: make the *plan* the reviewed, auditable artifact — a
deliberate governance gate in front of the AI execution agents. The pipeline
scaffold was there; the product wasn't yet. **It is now.** A plan goes in one end
and governed, tagged GitHub epics come out the other — and every step in between is
grounded in your real infrastructure.

Here's the whole thing, captured from the running portal:

<p>
  <img src="{{ '/static/img/pfactory-walkthrough.gif' | relative_url }}" alt="PFactory portal walkthrough" style="width:100%;max-width:900px;border-radius:12px;border:1px solid rgba(184,187,38,0.25);">
</p>

The full video + a screenshot gallery live on the [home page]({{ '/#see-it-in-action' | relative_url }}).

### Feasibility you can trust — cost · time · technical access

This is the part no self-planning coding agent does. PFactory doesn't just ask
*"is this plan well-shaped?"* — it asks *"can we actually build it, and what will it
cost?"* against the cloud it can reach:

- **Cost** — it derives the proposed resource shape and prices it through the real
  cloud pricing APIs (AWS Price List, Azure Retail, GCP Catalog), with a static
  fallback so an estimate always exists. In the demo a multi-region EKS SOW comes
  back at **≈ $2,470/month** with a per-line breakdown and a confidence label —
  real on-demand prices for the account's actual instances.
- **Technical access** — it runs **`iam:SimulatePrincipalPolicy`** for the actions
  the plan implies (`eks:CreateCluster`, `rds:CreateDBInstance`, …) and reports
  grant/deny per action. A plan you *can't* execute is flagged before anyone starts.
- **Time** — a calibrated effort rollup (story points → dev-day band) with its
  assumptions stated, so the number is auditable, not magic.

A cost over the configured budget is an advisory that routes the plan to a human —
it never silently blocks.

### Grounded in your real systems

Enrichment introspects your **running Kubernetes / OpenShift / Azure / AWS / GCP**
read-only and surfaces it as *AI Context*: live resource counts, regions,
and — in the demo — two security groups genuinely open to `0.0.0.0/0`, pulled
straight from the account. When a best-practice MCP (Terraform, Azure, …) would
help but isn't installed, PFactory emits a **cited "suggest-install" advisory**
with the exact command rather than failing.

### It honours your document

Upload a SOW or architecture doc and PFactory **never rewrites it.** It attaches
**cited, anchored suggestions** to the original — *why* each change, with a link to
the source — and offers an improved draft beside it. You accept, reject, or adopt.

### Pick a category, pick a template

Plans now have **categories** (product · software · feature · hosting ·
infrastructure · testing · CI/CD) and **Backstage-compatible templates** that carry
an embedded `policy:` block. Choose one at intake and its required tags / regions /
IAM / security baselines are enforced through the review gates — opt-in, so a clean
plan is never penalised for a label it didn't pick.

### Hand a plan off from anywhere

PFactory is **MCP- and API-first.** A new set of `mcp__pfactory__plan_*` tools
(`plan_categories`, `plan_ingest`, `plan_process`, `plan_status`, `plan_get`,
`plan_approve`) lets **Claude Code, Antigravity, and Codex/Copilot** hand a plan
in and track it to approval — or use the identical HTTP API. Handoff arrives on a
dedicated `agent` channel.

### Plans live on the board

The portal's board shows every plan moving through the workflow —
**Plans ready → In Progress → AI Review → Human Review → Done** — bucketed
automatically by its state. Open a card to see feasibility, the cited review, the
suggestions, and to approve or emit.

### Governed emit — the "secret language" for AIFactory & TFactory

On **AI + human** approval, PFactory emits a GitHub **epic + child issues** tagged
with a documented taxonomy: the mandatory **`pfactory`** marker, `handoff:aifactory`
/ `handoff:tfactory` routing, `type:*` / `plan-type:*` / `priority:*`, and a
machine-readable `pfactory:meta` block (cost, effort, access, citations). AIFactory
picks them up and executes; TFactory tests them. The contract is published in
[`docs/tag-taxonomy.md`](https://github.com/olafkfreund/PFactory/blob/main/docs/tag-taxonomy.md),
with pickup issues already filed in both sibling repos.

### The throughline: help, never override

Every requested change carries a **why and a doc source**. Missing credentials,
regions, or MCP servers degrade to advisories — never errors. Side-effects (git,
PRs, emit) are **dry-run by default**, and nothing ships to AIFactory without a
human approving a plan that already passed the AI gates. PFactory gives engineers
and architects AI velocity *with* governance — it assists, it doesn't replace them.

It's all on `main` now (backend + portal, fully tested). Watch the
[walkthrough]({{ '/#see-it-in-action' | relative_url }}), read
[how it works]({{ '/architecture/' | relative_url }}), and tell us what your
golden-path templates should enforce.
