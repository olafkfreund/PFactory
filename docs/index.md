---
layout: default
title: PFactory
nav_order: 1
---

<section class="hero">
  <span class="hero__eyebrow">Plan&nbsp;·&nbsp;Govern&nbsp;·&nbsp;Hand&nbsp;off — the planning factory in front of the AI execution agents</span>
  <h1 class="hero__title">Plans, governed before a single line is built.</h1>
  <p class="hero__subtitle">
    Hand PFactory a project plan — uploaded as docx / pdf / markdown, or via the
    MCP control plane, the CLI, or a GitHub issue. PFactory enriches it with your
    <strong>live</strong> org and cloud context, decomposes it, runs
    architecture / security / best-practice / feasibility gates, and emits
    governed <strong>GitHub epics + child issues</strong> that AIFactory executes.
  </p>
  <p>
    <a class="hero__cta hero__cta--primary" href="{{ '/architecture/' | relative_url }}">
      How it works →
    </a>
    &nbsp;
    <a class="hero__cta" href="https://github.com/olafkfreund/PFactory/issues/1">
      Build epic ↗
    </a>
    &nbsp;
    <a class="hero__cta hero__cta--ghost" href="{{ '/roadmap/' | relative_url }}">
      Roadmap →
    </a>
  </p>
</section>

{% include stat-grid.html %}

{% include pipeline-diagram.html %}

## Why a planning factory

<div class="reveal" markdown="1">

The 2026 coding agents — Devin, Factory's droids, the Copilot coding agent —
collapsed planning *into* execution: hand them a vague ask and they plan and build
in one loop. That optimizes generation speed. But the real constraint is the
opposite: **the gap between what agents generate and what teams can confidently
approve.** PFactory makes the *plan* the reviewed, auditable artifact — so you get
AI velocity **with** human-grade governance, grounded in your real infrastructure.

</div>

## What PFactory does

<ul class="feature-row">
  <li class="feature-row__card reveal" style="--reveal-delay: 0ms">
    <span class="feature-row__icon" aria-hidden="true">📥</span>
    <h3>Ingest any plan</h3>
    <p>Upload docx / pdf / markdown, or drive it from the MCP control plane, the CLI, or a GitHub issue / discussion. Software plans get the deep path; any deliverable is welcome.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 80ms">
    <span class="feature-row__icon" aria-hidden="true">🛰️</span>
    <h3>Ground it in reality</h3>
    <p>Enrich with internal wikis &amp; <strong>Backstage</strong> (catalog, TechDocs, golden paths) and <em>read-only</em> introspection of running <strong>Kubernetes · OpenShift · Azure · AWS · GCP</strong> + <strong>Terraform</strong> — real load, quotas, policies.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 160ms">
    <span class="feature-row__icon" aria-hidden="true">🧱</span>
    <h3>Decompose &amp; define</h3>
    <p>Break the work into an epic + child issues with dependencies. For software, add a generated <strong>Testing Strategy</strong> and a <strong>CI/CD</strong> pipeline definition.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 240ms">
    <span class="feature-row__icon" aria-hidden="true">🛡️</span>
    <h3>Govern, then hand off</h3>
    <p>Mandatory <strong>architecture / security / best-practice / feasibility</strong> gates (deterministic policy-as-code + LLM lenses) and one human approval — then emit governed issues for AIFactory.</p>
  </li>
</ul>

## Plug in anything

<div class="reveal" markdown="1">

PFactory is extensible by design. A declarative registry lets you add **MCP
servers, skills, agents, and templates** without forking. Cloud and IaC
best-practice MCP servers (**AWS · Azure · GCP · Terraform**) drive automatic
review. Templates are **Backstage Software Template-compatible** and carry their
own embedded **policy rules** — a `gcp-project` template scaffolds the project
*and* enforces its org policies, regions, and IAM baselines. As the clouds and
best practices change, PFactory watches them and **proposes template updates via
pull request** — never silent edits.

</div>

## How it fits

<div class="reveal" markdown="1">

PFactory is the third factory in the suite — **plan → build → test**, governed
end to end:

| Factory | Role |
|---|---|
| **PFactory** | **Plans &amp; governs** — ingest, enrich, decompose, review, emit issues |
| [AIFactory](https://github.com/olafkfreund/AIFactory) | **Executes** — spec → plan → code → QA on the emitted issues |
| TFactory | **Tests** — generates &amp; runs tests on the built code |

</div>

## Documentation

<ul class="feature-row">
  <li class="feature-row__card reveal" style="--reveal-delay: 0ms">
    <span class="feature-row__icon">🏗️</span>
    <h3><a href="{{ '/architecture/' | relative_url }}">Architecture</a></h3>
    <p>The eight-stage planning pipeline and how it transforms inputs into governed issues.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 70ms">
    <span class="feature-row__icon">🧭</span>
    <h3><a href="{{ '/roadmap/' | relative_url }}">Roadmap</a></h3>
    <p>Phases P0–P8, the epic, and the 26 child issues that build PFactory.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 140ms">
    <span class="feature-row__icon">🎯</span>
    <h3><a href="{{ '/market-positioning/' | relative_url }}">Positioning</a></h3>
    <p>Where PFactory sits in the 2026 autonomous-engineering market, and the wedge.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 210ms">
    <span class="feature-row__icon">📰</span>
    <h3><a href="{{ '/blog/' | relative_url }}">Blog</a></h3>
    <p>Notes on AI planning, governance gates, and live-infrastructure grounding.</p>
  </li>
</ul>

## Tracking

- **Epic + sub-issues** → [github.com/olafkfreund/PFactory/issues](https://github.com/olafkfreund/PFactory/issues)
- **Source** → [github.com/olafkfreund/PFactory](https://github.com/olafkfreund/PFactory)
- **Executes the plans** → [github.com/olafkfreund/AIFactory](https://github.com/olafkfreund/AIFactory)
- **License** → [MIT OR GPL-3.0](https://github.com/olafkfreund/PFactory/blob/main/LICENSE)
