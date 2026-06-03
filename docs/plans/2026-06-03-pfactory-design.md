# PFactory (Plan Factory) — Design Spec

> Created: 2026-06-03
> Status: Approved — v0.1 in build
> Authors: super-brainstorm interview

## Summary

PFactory is the **planning & governance incubator** that sits in front of the AI
execution agents. It is the third factory in the suite:

- **AIFactory** — executes software tasks (spec → plan → code → QA).
- **TFactory** — generates & runs tests.
- **PFactory** — **plans & governs**: ingests project plans, enriches them with
  live organizational context, runs review gates, and emits governed GitHub epics
  + child issues that AIFactory executes.

It is built by cloning and transforming the TFactory skeleton (Python
`apps/backend` + FastAPI `apps/web-server` + React `apps/frontend-web` + MCP
server + Helm + Docker), ~80% reuse.

## Why now (market wedge)

In 2026 the autonomous-coding market (Devin/Cognition, Factory droids, Copilot
coding agent, Tembo) collapsed planning *into* execution — agents self-plan from a
vague ask. The industry's own stated 2026 constraint is the opposite of raw
generation speed: **AI quality / governance / verification** — the gap between
what agents generate and what teams can *confidently approve*. No vendor sells a
dedicated planning-and-governance layer *in front of* the execution agents that
(a) enriches plans with live org context, (b) runs architecture/security/
best-practice/feasibility gates, and (c) emits governed, execution-ready issues.
That is PFactory's position: **the trust & context layer before AIFactory.** Full
analysis in [`../market-positioning.md`](../market-positioning.md).

## Architecture — planning pipeline

`Ingest → Enrich/Discover → Detect → Decompose → CI/CD+Testing Synthesis →
Review-Gates → Human-Approval → Emit`. New code under `apps/backend/plan/`; each
stage transforms a TFactory donor module.

| Stage | New module | Donor |
|-------|-----------|-------|
| Ingest | `plan/ingest/sources.py` | `apps/backend/spec_sources.py` (+ pypdf, python-docx) |
| Enrich/Discover | `plan/enrich/infra_adapters/`, `plan/enrich/knowledge/` | `agents/cloud/discovery.py`, `project/stack_detector.py` |
| Detect | `plan/detect/target_classifier.py` | `project/stack_detector.py` |
| Decompose | `plan/decompose/planner.py` | `agents/planner.py` + `agents/session.py` |
| Synthesize | `plan/synthesize/{cicd_generator,testing_strategy}.py` | net-new (informed by `analysis/ci_discovery.py`) |
| Review-gates | `plan/review/gates.py`, `plan/review/lenses/*` | `agents/evaluator.py` + `agents/triager.py` |
| Human-approval | `plan/review/state.py` | `apps/backend/review/state.py` |
| Emit | `plan/emit/{github_emitter,aifactory_handoff}.py` | `runners/github/providers/github_provider.py`, `tools/git_writer.py` |

## Extensibility — provider MCP + template/rules registry

- **Provider MCP layer** (`plan/providers/`): PFactory executes & uses cloud +
  IaC best-practice MCP servers — AWS, Azure, GCP, **HashiCorp Terraform**, +
  pluggable others (with Prowler / Checkov / OPA / cloud Well-Architected) —
  during Enrich and Review.
- **Registry** (`plan/registry/`): declarative catalogue of pluggable MCP
  servers, skills, agents, and templates (id, kind, version, capabilities,
  enabled). Extend by config, not fork.
- **Templates** (`templates/<kind>/<name>/`): **Backstage Software Template
  (`template.yaml`) compatible** plus an embedded `policy:` block (required
  tags/regions/IAM/security baselines). Kinds: service, software, architecture
  (e.g. `gcp-project` scaffolds *and* enforces rules).
- **Rules engine — hybrid**: deterministic policy-as-code (Checkov / OPA-Rego /
  cloud-native via MCP) **+** LLM review lenses.
- **Living templates** (`plan/templates/updater.py`): watches the clouds, detects
  drift, and **opens a PR** proposing template/rule updates — never silent edits.

## Decisions (interview)

1. Full-parity product (clone + rebrand). 2. Handoff = GitHub epic/child issues +
optional AIFactory API trigger. 3. Mandatory automated gates + one human approval.
4. Four live read-only infra adapters (K8s, OpenShift, Azure, AWS/GCP). 5. All
knowledge connectors (Backstage, Confluence, GitBook, local/Git, Notion/
SharePoint). 6. Testing + CI/CD as dedicated child issues + spec docs (PFactory
*generates* CI/CD). 7. Public repo under `olafkfreund`, MIT/GPL like TFactory. 8.
All intake channels (portal, CLI, MCP, GitHub). 9. General intake + software
deep-path. 10. Generic factory chaining with TFactory-aware hooks. 11. Templates =
Backstage Scaffolder + embedded policy. 12. Hybrid rules engine. 13. Template
updates auto-detected → PR-proposed.

## Data contracts

- **`NormalizedPlan`** — `plan_id` (`NNN-slug`), `title`, `description`,
  `source_format`, `source_channel`, `criteria[]`, `target_kind`, `plan_type`,
  `enrichment{infra,knowledge}`, `content_hash`, `ingested_at`.
- **`PlanReview`** — `lenses[]{lens,score,max,findings,blocking}`, `threshold`,
  `aggregate_score`, `gates_passed`, `code_gates_applied`, `human_approval{…hash
  invalidation…}`.
- **AIFactory handoff** — `{title, description, metadata{complexity,
  githubIssueNumber, model, thinkingLevel, requireReviewBeforeCoding, phaseModels,
  phaseThinking}}` → write `.aifactory/specs/{NNN-slug}/requirements.json`
  (preferred) or POST `/api/tasks/create-and-run` or labelled GitHub issue.

## Roadmap

P0 clone+rebrand · P1 ingest (pdf/docx + channels) · P2 detect+decompose ·
P3 enrich adapters (k8s→oc/azure/aws/gcp; knowledge) · P4 CI/CD+testing synthesis ·
P5 review gates + human approval · P6 emit + AIFactory handoff · P7 portal/Helm
ship · P8 provider MCP + template/rules registry.

## Verification

Phase-0 gate: pytest green post-rebrand, MCP server starts
(`scripts/start-pfactory-mcp.sh`), web-server boots, portal builds. Ingest:
round-trip docx/pdf/md → `NormalizedPlan`. Decompose: golden plan → `EpicPlan`.
Gates: good/bad fixtures assert `gates_passed`; hash edit invalidates approval.
Emit: dry-run prints epic/children + `requirements.json` without external calls.
E2E: upload → review → approve → emit (dry-run) → assert AIFactory-ready payloads.
