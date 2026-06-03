# Product Mission

> Last Updated: 2026-05-30
> Version: 1.0.0

## Pitch

PFactory is an autonomous test-generation platform that helps engineering teams
ship features with trustworthy tests by generating tests aligned to a feature's
acceptance criteria, running them in a sandbox, and validating their quality
with a 5-signal verdict pipeline — not just a coverage number.

## Users

### Primary Customers

- **AIFactory teams (the warm-start wedge):** developers who finish a feature on
  a branch and want aligned tests generated and triaged onto the same PR via
  `/handover-to-pfactory`.
- **Quality-conscious engineering teams (the standalone market):** teams that
  distrust low-value AI-generated tests and want evidence — mutation kills,
  stability history, semantic relevance — before they trust a test.

### User Personas

**Feature Developer** (25-45 years old)
- **Role:** Software engineer shipping features on a branch
- **Context:** Has a finished feature with acceptance criteria; writing tests is
  the tax they keep deferring
- **Pain Points:** hand-writing tests is slow; AI test tools emit low-value
  assertion tests and flaky tests that erode trust
- **Goals:** get meaningful, passing tests aligned to the AC, surfaced on the PR,
  without babysitting

**Reviewer / Tech Lead** (30-50 years old)
- **Role:** Reviews PRs, owns quality gates
- **Context:** Wants feature + tests + a quality verdict in one review cycle
- **Pain Points:** can't tell if AI tests actually assert behavior; coverage
  lies; flaky tests waste CI
- **Goals:** a ranked, explained triage report (why each test matters) they can
  click straight from the PR comment

## The Problem

### AI-generated tests are distrusted

Most AI test tools generate from *code*, not *intent*, and prove quality with a
coverage number. Teams get low-value assertion tests, flaky tests, and false
confidence — so adoption stalls (industry data: ~89% pilot AI-QA, ~15% deploy
it widely).

**Our Solution:** generate to the acceptance criteria, then gate every test on
five signals (coverage delta · stability · mutation · flake-lint · LLM semantic
relevance) and triage the survivors into a ranked, explained report.

### Coverage ≠ quality

A test can execute every line and assert nothing. Buyers now expect mutation
scoring and semantic judgment.

**Our Solution:** mutation testing and semantic relevance are first-class signals
in the verdict, not afterthoughts.

## Differentiators

### Spec-aligned generation

Unlike tools that generate from code (Qodo, Cover-Agent, Diffblue), PFactory
generates to the feature's acceptance criteria. This produces tests that reflect
*intended* behavior, which is the highest whitespace in the 2025-26 market.

### 5-signal verdict + autonomous triage

Unlike single-signal tools, PFactory bundles coverage delta, 3× stability,
mutation kill, flake-lint promotion, and LLM semantic relevance, then a Triager
dedups and ranks the survivors — answering the #1 trust complaint (test dumps)
with an explained shortlist.

### Safe and self-hostable

Tests run in a `--network=none --read-only` sandbox; side effects (git commit, PR
comment) are dry-run by default. The provider factory runs against Claude,
Ollama, vLLM, or any OpenAI-compatible endpoint — a credible BYO-LLM / air-gapped
story for regulated teams.

## Key Features

### Core Features

- **Spec-aligned Planner:** turns acceptance criteria + diff into a lane-tagged
  test plan across the v0.2 spine (unit / browser / api / integration / mutation).
- **Polyglot generation:** pytest (Python) + Jest & Playwright (TypeScript) at
  v0.2, via a framework descriptor registry.
- **Sandboxed Executor:** per-framework Docker runners; Browser lane runs against
  an `AppRuntime` (compose + health-poll).
- **5-signal Evaluator:** structurally separate from generation (research-mandated
  non-self-validation), emitting per-test verdicts.
- **Autonomous Triager:** dedups, ranks, and renders a PR-ready report with
  evidence links (screenshots / video / trace / HAR).

### Trust & Workflow Features

- **Hallucination + flake guards:** preflight import resolution + AST flake-lint
  reject bad tests before they're committed.
- **Test evidence capture:** Browser/API failures capture screenshots, video,
  trace.zip, and network.har, served by the portal.
- **PR-native, dry-run-by-default side effects:** commit-to-branch + PR comment,
  opt-in only.
- **BYO-LLM provider factory:** Claude SDK primary; Ollama / vLLM / LocalAI /
  OpenAI-compatible for self-hosted/air-gapped runs.
