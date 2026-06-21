---
layout: default
title: Portal Tour
permalink: /gallery/
---

# Portal tour

A captioned walk through the live PFactory planning portal — every main view in the
left navigation, the key dialogs, and a few real plans, including both a flagged
"needs attention" state and a verified, governed one. Every image below was
captured from the running portal at
[pfactory.freundcloud.org.uk](https://pfactory.freundcloud.org.uk/); click any
shot to open it full size.

<style>
.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 1.5rem;
  margin: 1.5rem 0 2.5rem;
}
.gallery-grid figure { margin: 0; }
.gallery-grid img {
  width: 100%;
  border-radius: 10px;
  border: 1px solid rgba(255,255,255,0.10);
  box-shadow: 0 6px 30px rgba(0,0,0,0.35);
  display: block;
}
.gallery-grid figcaption {
  margin-top: 0.6rem;
  font-size: 0.92rem;
  opacity: 0.82;
  line-height: 1.45;
}
.gallery-grid figcaption strong { opacity: 1; }
</style>

## Planning

<div class="gallery-grid" markdown="0">
  <figure>
    <a href="{{ '/static/img/screenshots/20-board.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/20-board.png' | relative_url }}" alt="Planning Portal Kanban board" loading="lazy">
    </a>
    <figcaption><strong>Planning board.</strong> Plans flow left to right — Plans ready, Human Review, Done — with issue counts and per-plan flags such as "needs attention".</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/21-board-list.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/21-board-list.png' | relative_url }}" alt="Planning Portal list view with session statuses" loading="lazy">
    </a>
    <figcaption><strong>List view.</strong> The same plans as sessions with status badges — <em>emitted / gates ok</em>, <em>processed / gates fail</em>, <em>undetermined</em> — alongside the registry, templates and providers configuration.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/22-plan-needs-attention.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/22-plan-needs-attention.png' | relative_url }}" alt="A plan flagged needs attention with pipeline logs" loading="lazy">
    </a>
    <figcaption><strong>A flagged plan.</strong> The "LinkLite" 3-tier AWS plan opened on its pipeline tab, showing the processing logs and acceptance criteria that drove the gate decision.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/23-plan-done.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/23-plan-done.png' | relative_url }}" alt="A verified, governed plan in the Done column" loading="lazy">
    </a>
    <figcaption><strong>A verified plan.</strong> The "Task Board service" plan marked verified, with its synthesized artifacts and the acceptance criteria each gate checked.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/24-new-plan.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/24-new-plan.png' | relative_url }}" alt="New plan ingest dialog" loading="lazy">
    </a>
    <figcaption><strong>Ingest a plan.</strong> Upload a <code>.pdf</code> / <code>.docx</code> / <code>.md</code> document, or paste text, to start the planning pipeline.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/25-task.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/25-task.png' | relative_url }}" alt="Create new task wizard" loading="lazy">
    </a>
    <figcaption><strong>Drive a task.</strong> Describe the work, pick Quick or Full mode, and tune the per-phase model and thinking level via the agent profile.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/32-project-picker.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/32-project-picker.png' | relative_url }}" alt="Project picker dropdown" loading="lazy">
    </a>
    <figcaption><strong>Project picker.</strong> Switch between planning workspaces, or add a new one, without leaving the board.</figcaption>
  </figure>
</div>

## Grounding and extensibility

<div class="gallery-grid" markdown="0">
  <figure>
    <a href="{{ '/static/img/screenshots/29-index-memory.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/29-index-memory.png' | relative_url }}" alt="Project index and memory view" loading="lazy">
    </a>
    <figcaption><strong>Index &amp; Memory.</strong> AI-discovered knowledge about the existing codebase — the foundation for code-aware, brownfield planning.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/26-files.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/26-files.png' | relative_url }}" alt="Workspace file explorer" loading="lazy">
    </a>
    <figcaption><strong>Files.</strong> Browse the planning workspace directly — the same files the planner reads when grounding a plan in the real repository.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/27-mcp.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/27-mcp.png' | relative_url }}" alt="MCP server overview" loading="lazy">
    </a>
    <figcaption><strong>MCP servers.</strong> Toggle Context7, Graphiti memory, Playwright and the PFactory tools per project, and assign servers to individual agents.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/28-skills.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/28-skills.png' | relative_url }}" alt="Skills browser" loading="lazy">
    </a>
    <figcaption><strong>Skills.</strong> The extensible skill registry, grouped by category — devtools, engineering, infra, monitoring, operations, workflow.</figcaption>
  </figure>
</div>

## Emit and observe

<div class="gallery-grid" markdown="0">
  <figure>
    <a href="{{ '/static/img/screenshots/30-plans.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/30-plans.png' | relative_url }}" alt="Plans / GitHub sync view" loading="lazy">
    </a>
    <figcaption><strong>Plans.</strong> The emitted-issues view. Connect a GitHub token in project settings to sync governed epics and child issues to a repository.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/31-github-prs.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/31-github-prs.png' | relative_url }}" alt="GitHub pull requests view" loading="lazy">
    </a>
    <figcaption><strong>GitHub PRs.</strong> Track pull requests on the connected repository — filter by contributor and status — as AIFactory builds against the emitted plan.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/33-settings.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/33-settings.png' | relative_url }}" alt="Settings dialog" loading="lazy">
    </a>
    <figcaption><strong>Settings.</strong> Agent profiles, LLM providers, integrations, git and test credentials, cloud assessment and documentation targets — all in one place.</figcaption>
  </figure>
  <figure>
    <a href="{{ '/static/img/screenshots/34-chat.png' | relative_url }}">
      <img src="{{ '/static/img/screenshots/34-chat.png' | relative_url }}" alt="Insights chat panel" loading="lazy">
    </a>
    <figcaption><strong>Insights chat.</strong> Ask questions about the codebase, request improvements, or surface security concerns — and turn the answers into a new task.</figcaption>
  </figure>
</div>
