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
    <a class="hero__cta" href="{{ '/market-positioning/' | relative_url }}">
      Why PFactory →
    </a>
    &nbsp;
    <a class="hero__cta hero__cta--ghost" href="{{ '/roadmap/' | relative_url }}">
      Roadmap →
    </a>
  </p>
</section>

{% include stat-grid.html %}

{% include pipeline-diagram.html %}

> **Part of the [Factory family](https://factory.freundcloud.com/)** — a governed, verified, observable autonomous software factory. **PFactory** plans · [AIFactory](https://aifactory.freundcloud.com/) builds · [TFactory](https://tfactory.freundcloud.com/) verifies · [CFactory](https://github.com/olafkfreund/CFactory) watches over all four. → **[Why Factory](https://factory.freundcloud.com/why/)**

## See it in action

<video controls preload="metadata" playsinline
       poster="{{ '/static/img/screenshots/20-board.png' | relative_url }}"
       style="width:100%;max-width:960px;border-radius:12px;border:1px solid rgba(184,187,38,0.25);box-shadow:0 8px 40px rgba(0,0,0,0.4);">
  <source src="{{ '/static/videos/pfactory-walkthrough.mp4' | relative_url }}" type="video/mp4">
  Your browser can't play embedded video —
  <a href="{{ '/static/videos/pfactory-walkthrough.mp4' | relative_url }}">download the walkthrough</a>.
</video>

<p style="opacity:0.8;margin-top:0.75rem;">
  <em>Ingest a plan → process → review across architecture · security · compliance · best-practice · feasibility · completeness lenses →
  red-team / adversarial pass → board → approve →
  <strong>emit tagged GitHub epics + issues</strong>. Captured from the live portal.</em>
</p>

<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:0.75rem;margin-top:1.25rem;">
{% assign shots = "20-board:Plans on the board,21-board-list:Plans in the list view,22-plan-needs-attention:A plan flagged needs-attention,23-plan-done:A verified, governed plan,29-index-memory:Code-aware project index,27-mcp:MCP servers per agent" | split: "," %}
{% for s in shots %}{% assign p = s | split: ":" %}
  <a href="{{ '/static/img/screenshots/' | append: p[0] | append: '.png' | relative_url }}" title="{{ p[1] }}">
    <img src="{{ '/static/img/screenshots/' | append: p[0] | append: '.png' | relative_url }}" alt="{{ p[1] }}"
         style="width:100%;border-radius:8px;border:1px solid rgba(255,255,255,0.08);" loading="lazy">
  </a>
{% endfor %}
</div>

<p style="margin-top:0.75rem;">
  <a class="hero__cta hero__cta--ghost" href="{{ '/gallery/' | relative_url }}">See the full portal tour →</a>
</p>

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
    <h3>Ingest any plan</h3>
    <p>Upload docx / pdf / markdown, or drive it from the MCP control plane, the CLI, or a GitHub issue / discussion. Software plans get the deep path; any deliverable is welcome.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 80ms">
    <h3>Read the code first</h3>
    <p><em>Read-only</em> reconnaissance of the target repo (static, blobless, never executes repo code) maps acceptance criteria to <strong>real files</strong> and takes the plan's language from the repo — no wrong-language traps. Then enrich with <strong>Backstage</strong>, wikis, and live <strong>Kubernetes · OpenShift · Azure · AWS · GCP · Terraform</strong>.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 160ms">
    <h3>Decompose &amp; define</h3>
    <p>Break the work into an epic + child issues with dependencies. For software, add a generated <strong>Testing Strategy</strong> and a <strong>CI/CD</strong> pipeline definition.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 240ms">
    <h3>Govern, then hand off</h3>
    <p>Mandatory <strong>architecture / security / compliance / best-practice / feasibility / completeness</strong> lenses, a <strong>hard readiness gate</strong> (hash-bound waivers) honouring your SpecKit <strong>constitution</strong>, and one human approval — then emit a <strong>signed Task Contract</strong> + governed issues for AIFactory.</p>
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

It also speaks **GitHub's native agentic surface**: free
[GitHub Models](/blog/2026/06/09/pfactory-speaks-githubs-agentic-language/)
inference (`github-models/openai/gpt-4.1`), `copilot:delegate` to hand a draft to
the **Copilot cloud agent**, and an **HTTP MCP server** (`POST /mcp`) that serves
the epic, requirements, decomposition, signed **Task Contract v2**, and review
status to whatever agent builds from the plan. All opt-in, all additive.

</div>

## How it fits

<div class="reveal" markdown="1">

PFactory is the third factory in the suite — **plan → build → test**, governed
end to end:

</div>

{% include factory-suite.html %}

<div class="reveal" markdown="1">

| Factory | Role |
|---|---|
| **PFactory** | **Plans &amp; governs** — ingest, enrich, decompose, review, emit issues |
| [AIFactory](https://github.com/olafkfreund/AIFactory) | **Executes** — spec → plan → code → QA on the emitted issues |
| TFactory | **Tests** — generates &amp; runs tests on the built code |

</div>

## Documentation

<ul class="feature-row">
  <li class="feature-row__card reveal" style="--reveal-delay: 0ms">
    <h3><a href="{{ '/architecture/' | relative_url }}">Architecture</a></h3>
    <p>The eight-stage planning pipeline and how it transforms inputs into governed issues.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 70ms">
    <h3><a href="{{ '/roadmap/' | relative_url }}">Roadmap</a></h3>
    <p>Phases P0–P8, the epic, and the 26 child issues that build PFactory.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 140ms">
    <h3><a href="{{ '/market-positioning/' | relative_url }}">Positioning</a></h3>
    <p>Where PFactory sits in the 2026 autonomous-engineering market, and the wedge.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 210ms">
    <h3><a href="{{ '/blog/' | relative_url }}">Blog</a></h3>
    <p>Notes on AI planning, governance gates, and live-infrastructure grounding.</p>
  </li>
</ul>

## Tracking

- **Epic + sub-issues** → [github.com/olafkfreund/PFactory/issues](https://github.com/olafkfreund/PFactory/issues)
- **Source** → [github.com/olafkfreund/PFactory](https://github.com/olafkfreund/PFactory)
- **Executes the plans** → [github.com/olafkfreund/AIFactory](https://github.com/olafkfreund/AIFactory)
- **License** → [MIT OR GPL-3.0](https://github.com/olafkfreund/PFactory/blob/main/LICENSE)
