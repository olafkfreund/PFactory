---
layout: default
title: PFactory
nav_order: 1
---

<section class="hero">
  <span class="hero__eyebrow"><a href="https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0">v0.2.0</a> · 5 lanes + evidence capture · <a href="{{ '/credentials/' | relative_url }}">Credential Broker</a> — vault-backed cloud auth · runs on any LLM</span>
  <h1 class="hero__title">Autonomous tests, AI-graded.</h1>
  <p class="hero__subtitle">
    Hand PFactory a finished feature on a branch — from AIFactory, Claude Code,
    or any tool, via the MCP control plane or a plain acceptance-criteria file
    (markdown / Gherkin / EARS). The agent pipeline plans, writes, sandboxes,
    scores, and commits the suite — autonomously — then posts a triage report
    to your PR.
  </p>
  <p>
    <a class="hero__cta hero__cta--primary" href="{{ '/showcase/' | relative_url }}">
      See the demo →
    </a>
    &nbsp;
    <a class="hero__cta" href="https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0">
      v0.2.0 release ↗
    </a>
    &nbsp;
    <a class="hero__cta hero__cta--ghost" href="{{ '/design-plan/' | relative_url }}">
      Design plan →
    </a>
  </p>
</section>

{% include stat-grid.html %}

{% include pipeline-diagram.html %}

## How it works

<ul class="feature-row">
  <li class="feature-row__card reveal" style="--reveal-delay: 0ms">
    <span class="feature-row__icon" aria-hidden="true">🪶</span>
    <h3>Spec-aware handover</h3>
    <p>A Claude Code session in your AIFactory repo runs <code>/handover-to-pfactory</code>. PFactory snapshots the spec + diff, runs five agents, returns a verdicts report.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 80ms">
    <span class="feature-row__icon" aria-hidden="true">🛡️</span>
    <h3>Two-layer guardrails</h3>
    <p>Pre-flight static-checks every <code>import</code> resolves. Flake-risk lint catches dict-iteration order, time.sleep, datetime.now without freeze.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 160ms">
    <span class="feature-row__icon" aria-hidden="true">🔬</span>
    <h3>5-signal verdicts</h3>
    <p>Coverage delta · 3× stability re-run · mutate-and-check probe · flake-lint promotion · LLM semantic relevance. Survived-mutation tests don't ship.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 240ms">
    <span class="feature-row__icon" aria-hidden="true">📨</span>
    <h3>Dry-run by default</h3>
    <p>Per <code>CLAUDE.md</code> no-auto-push policy: git_writer + gh pr comment record argvs without executing. Operators opt in via env vars.</p>
  </li>
</ul>

## New since v0.2.0 — connect to anything

<div class="reveal" markdown="1">

Three capabilities make PFactory usable beyond a single laptop and a single
model: it can now **assess your cloud infrastructure**, **authenticate to your
cloud**, and **run on whatever LLM you already pay for**.

</div>

<ul class="feature-row">
  <li class="feature-row__card reveal">
    <span class="feature-row__icon" aria-hidden="true">☁️</span>
    <h3>Cloud infrastructure testing</h3>
    <p><strong>Problem:</strong> misconfigurations — public buckets, over-privileged IAM, management ports open to the internet — slip past code review and never fail a test.</p>
    <p><strong>Solution:</strong> a read-only assessment lane for <strong>AWS · GCP · Azure</strong>: access gate → discovery → Mermaid topology → Prowler/CIS scan (OCSF) → accept / flag / reject verdict → a downloadable remediation plan. Launch it from <code>+Task → Cloud Infrastructure</code>; reports land in the portal. <a href="https://github.com/olafkfreund/PFactory/blob/main/guides/cloud-testing.md">See how →</a></p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 80ms">
    <span class="feature-row__icon" aria-hidden="true">🔐</span>
    <h3>Credential Broker</h3>
    <p><strong>Problem:</strong> agents need real cloud/K8s/API credentials to test against live services, but secrets must never touch the repo.</p>
    <p><strong>Solution:</strong> resolve secrets from a vault (Azure KV · AWS Secrets Manager · GCP Secret Manager · HashiCorp Vault) or local sops/age/agenix, materialise them ephemerally (0600, wiped per task), gated by an explicit <strong>egress opt-in</strong> with an honest manifest. <a href="{{ '/credentials/' | relative_url }}">See how →</a></p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 160ms">
    <span class="feature-row__icon" aria-hidden="true">🧠</span>
    <h3>Run on any LLM</h3>
    <p><strong>Problem:</strong> teams are locked to one provider, or can't send code to a managed cloud at all.</p>
    <p><strong>Solution:</strong> a model-string-driven provider factory — Claude SDK, OpenAI Codex, Gemini CLI, GitHub Copilot CLI, Ollama (local), and any OpenAI-compatible endpoint (vLLM / LM Studio / OpenRouter…). Per-phase routing + an honest <a href="https://github.com/olafkfreund/PFactory/blob/main/guides/byo-llm.md">data-egress badge</a> for air-gapped / BYO-LLM runs.</p>
  </li>
</ul>

## Status by lane

<div class="reveal" markdown="1">

v0.2 replaced the v0.1 pipeline-stage decomposition (Functional / SAST /
DAST / Fuzz / Mutation) with a **modality-based spine** per Decision 2.
Security scanning is delegated to dedicated security pipelines and is
out of scope here — PFactory focuses on functional + feature testing.

| Lane | v0.2.0 status | Runtime | Coverage | Evidence captured |
|---|---|---|---|---|
| **Unit** | ✅ Active | `pfactory-runner-pytest` + `pfactory-runner-jest` | line (cobertura / lcov) | — |
| **Browser** | ✅ Active | `pfactory-runner-playwright` + AppRuntime (docker-compose + health-poll) | `null` (per Decision 11 — line coverage doesn't apply when the test drives the browser) | screenshots · video · trace.zip |
| **API** | ✅ Active | per-framework Docker image + HTTP HAR recorder | line where the test exercises framework code | network.har |
| **Integration** | ✅ Active | per-framework Docker image + AppRuntime (multi-service compose) | line where applicable | network.har · service logs |
| **Mutation** | ✅ Active | `mutmut` (Python) / Stryker (TypeScript) — one-mutation-per-run probe in the Evaluator | reported per mutant (killed / survived) | — |

All five lanes are wired and ship with v0.2.0. Each subtask's lane is
chosen by the Planner based on its `(language, framework)` via the
[framework registry](framework-registry/); reviewers see the lifecycle
phases (`executor_app_running`, `app_not_healthy`, etc.) in the
LaneStatusGrid and the per-test evidence in the Triager PR comment.

The v0.2 design doc enumerates a longer "future-ramp" set (Go / Rust /
Ruby support, additional security pipelines via integration) — those
hook into the existing 5-lane spine through new
`FrameworkDescriptor`s and don't require lane additions.

</div>

## Quickstart

<div class="reveal" markdown="1">

```bash
# Clone + bootstrap (NixOS / flake-based)
git clone https://github.com/olafkfreund/PFactory
cd PFactory
nix develop
pfactory-minimal-venv   # creates apps/backend/.venv
pfactory-test           # 531 backend tests, ~10s
```

Full walkthrough in the [repo README](https://github.com/olafkfreund/PFactory/blob/main/README.md) plus the [end-to-end smoke guide](https://github.com/olafkfreund/PFactory/blob/main/guides/e2e-smoke.md) for running real scenarios against an AIFactory project.

</div>

## Documentation

<ul class="feature-row">
  <li class="feature-row__card reveal" style="--reveal-delay: 0ms">
    <span class="feature-row__icon">🏗️</span>
    <h3><a href="{{ '/architecture/' | relative_url }}">Architecture</a></h3>
    <p>Directory structure, workspace layout, dataflow.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 70ms">
    <span class="feature-row__icon">🧭</span>
    <h3><a href="{{ '/design-plan/' | relative_url }}">Design plan</a></h3>
    <p>10 locked decisions, landscape research, risk register.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 140ms">
    <span class="feature-row__icon">📜</span>
    <h3><a href="{{ '/spec/' | relative_url }}">Spec</a></h3>
    <p>Agent OS spec: overview, user stories, deliverables.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 210ms">
    <span class="feature-row__icon">🔧</span>
    <h3><a href="{{ '/technical-spec/' | relative_url }}">Technical spec</a></h3>
    <p>Per-component implementation detail.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 280ms">
    <span class="feature-row__icon">🧪</span>
    <h3><a href="{{ '/tests/' | relative_url }}">Test coverage</a></h3>
    <p>TDD plan: unit / integration / e2e pyramid.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 350ms">
    <span class="feature-row__icon">📋</span>
    <h3><a href="{{ '/tasks/' | relative_url }}">Tasks</a></h3>
    <p>All 12 MVP tasks, dependency graph, GitHub issues.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 420ms">
    <span class="feature-row__icon">📈</span>
    <h3><a href="{{ '/progress/' | relative_url }}">Progress</a></h3>
    <p>Live build log, closed tasks + commits.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 490ms">
    <span class="feature-row__icon">⚡</span>
    <h3><a href="https://github.com/olafkfreund/PFactory/blob/main/CHANGELOG.md">Changelog</a></h3>
    <p>v0.2.0 release notes (16 task summaries), v0.1.0-mvp history, sharp edges.</p>
  </li>
</ul>

## Tracking

- **Epic + sub-issues** → [github.com/olafkfreund/PFactory/issues](https://github.com/olafkfreund/PFactory/issues)
- **Source** → [github.com/olafkfreund/PFactory](https://github.com/olafkfreund/PFactory)
- **Sister project (upstream)** → [github.com/olafkfreund/AIFactory](https://github.com/olafkfreund/AIFactory)
- **License** → [MIT OR GPL-3.0](https://github.com/olafkfreund/PFactory/blob/main/LICENSE)
