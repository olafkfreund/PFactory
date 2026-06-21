---
layout: default
title: Architecture
permalink: /architecture/
nav_order: 7
---

# Architecture

PFactory is the **feasibility & governance gate** between an AI plan and execution.
It takes a plan — including the output of spec-authoring tools (GitHub Spec-Kit,
AWS Kiro, BMAD-METHOD) — grounds it in your **live cloud**, proves it is
*buildable and affordable* before any code (cost · IAM access · quotas), gates it on
architecture / security / best-practice / feasibility lenses, takes one human
approval, and emits governed, execution-ready GitHub issues. Each of the eight
stages is a self-contained unit with a clear input and output, so the plan can be
inspected, reviewed, and audited at every step.

## The pipeline

`Ingest → Recon (code-aware) → Enrich/Discover → Detect → Decompose →
CI/CD + Testing synthesis → Deployment derivation → Feasibility + Review-gates →
Human-approval → Emit`

| # | Stage | What it does |
|---|-------|--------------|
| 1 | **Ingest** | Parse an uploaded plan (docx / pdf / markdown) or one delivered via the MCP control plane, the CLI, or a GitHub issue, into a normalized model (`NormalizedPlan`). |
| 2 | **Recon (code-aware)** | For a plan that targets an existing repo: a **static, read-only, never-executed** checkout (shallow / single-branch / blobless, hooks disabled, https-only, size/time caps, always torn down) builds a `RepoMap` — detected languages, a static Terraform / Helm / Kubernetes inventory, and a `change_mode` (greenfield / modify / migration). The planning language is reconciled against what the repo actually uses, and acceptance criteria map to real files. (RFC-0010.) |
| 3 | **Enrich / Discover** | Pull *live* context: internal wikis &amp; Backstage (catalog, TechDocs, golden-path templates) and *read-only* introspection of running Kubernetes / OpenShift / Azure / AWS / GCP + Terraform — workloads, resource limits, HPA, network policies, quotas, live load. |
| 4 | **Detect** | Classify the deliverable as software vs non-software. Software unlocks the deep path (task breakdown + testing + CI/CD + code gates); other deliverables get review + decomposition. |
| 5 | **Decompose** | Turn the plan into an epic + child issues with dependencies (`EpicPlan`). A per-project **constitution** (if declared) is injected here and hard-checked against the result. (RFC-0015.) |
| 6 | **Synthesize** | For software: generate a Testing Strategy and a CI/CD pipeline definition, each as a doc plus a dedicated child issue. |
| 7 | **Deployment derivation** | Derive a `deployment` block on the Task Contract from the recon inventory — how and where the work is expected to run — so the contract a builder and tester receive targets the right environment. (RFC-0013; the live deploy-then-verify loop runs as a dry-run lane in the wider program.) |
| 8 | **Feasibility + Review-gates** | **Feasibility:** price the proposed resource shape through the real cloud pricing APIs (AWS Price List · Azure Retail · GCP Catalog, static fallback), run `iam:SimulatePrincipalPolicy` for the actions the plan implies (grant/deny per action), and roll up a calibrated effort band — *before any code exists*. **Gates:** architecture / security / best-practice / feasibility lenses plus an adversarial **Red Team** lens that attacks the spec for missing failure modes, unstated assumptions, and criteria a broken build could satisfy — a hybrid of deterministic policy-as-code (Checkov · OPA · cloud-native policy via MCP) and LLM reviewers, scored against a threshold. An over-budget or access-denied plan routes to a human — it never silently blocks. (Red Team: RFC-0015.) |
| 9 | **Human-approval** | A single human approval gate. Gates must pass first; any edit to the plan invalidates the approval (content-hash check). |
| 10 | **Emit** | Create the GitHub epic + child issues (the durable source of truth) and hand off to AIFactory — by writing a `requirements.json`, triggering its API, or via a labelled issue. PFactory can also emit spec-kit-compatible spec / plan / tasks Markdown (RFC-0015). A completion event is emitted on every terminal session, gated on evidence (see below). |

## Data contracts

- **`NormalizedPlan`** — the ingested plan: id, title, description, source format
  &amp; channel, acceptance criteria, target kind, plan type, the enrichment
  bundle (infra + knowledge), and a content hash.
- **`PlanReview`** — per-lens scores and findings, the aggregate vs threshold,
  whether code gates applied, and the human-approval record (with hash
  invalidation).
- **`PlanReadiness`** — the hard completeness gate, orthogonal to the lens score
  so a high aggregate cannot mask missing information. A set of named checks
  (children/criteria present, acceptance-criterion to child coverage, sound
  dependencies, access granted/verified, enrichment integrity, no blocking
  findings, decomposition trustworthiness, criterion testability), each with a
  severity, reason, remediation, and evidence. A hard fail blocks emission unless
  a human records an audited waiver bound to the plan hash. See the
  [Planning and Trust]({{ '/planning-and-trust/' | relative_url }}) guide.
- **Task Contract** — the signed RFC-0002 contract handed to AIFactory carries,
  in addition to the plan and execution profile, the `tfactory` test lanes, the
  RFC-0005 `environment` manifest (per-task toolchain + Nix provisioning; see the
  fleet guide "Reproducible test environments"), the RFC-0007 `access` block
  (auth requirements, broker refs only — never secrets), a `baseline` block
  (RepoMap summary + blast radius from the recon stage, plus `provenance.base_ref`
  / `baseline_commit`), and an RFC-0013 `deployment` block derived from the
  target's live deployment reality. The per-phase execution model is routed on a
  capability-vs-cost score (RFC-0014), overridable by a pinned agent profile.
- **AIFactory handoff** — `{ title, description, metadata }` written to
  `.aifactory/specs/{id}/requirements.json` (preferred), POSTed to
  `/api/tasks/create-and-run`, or attached to a labelled GitHub issue.
- **Completion event** — the normalized terminal envelope
  (`{ correlation_key, service, task_id, status, phase, updated_at, correlation, usage }`)
  emitted when a session lands on a terminal status, for CFactory observability and
  AIFactory correlation. It is **evidence-gated (RFC-0001a):** a session is only
  reported `emitted` if the emit actually created the epic GitHub issue; an
  `emitted` session with no epic issue produced no governed work item and is
  downgraded to `failed` with `halt_reason: "no_evidence: emit created no issues"`,
  so nothing renders a plan that created nothing as green. An additive `evidence`
  block `{ proof_kind: "issues", epic_issue, child_count }` rides along as proof.

## Session API and the plan DAG

`GET /api/plan/sessions/{id}` returns the full session via the Pydantic
`model_dump()`, which includes the decomposed `epic` (`EpicPlan`) and its
`children`. Each child (`ChildIssue`) carries a stable `key`, `title`, `kind`
(`feature` / `task` / `testing` / `cicd` / `docs` / `infra` / `research` /
`chore`), and `depends_on` — a list of sibling keys forming the dependency graph.
This makes the plan a directed acyclic graph (validated for dangling deps,
self-deps, and cycles before emission) that is **public on the session API**.
[CFactory](https://github.com/olafkfreund/CFactory) consumes it to render the plan
stage of its live execution diagram as a dependency graph, without any
PFactory-side change for the diagram — the data was already exposed.

## Extensibility

A declarative **registry** lets you add MCP servers, skills, agents, and
templates without forking. **Provider MCP servers** (AWS · Azure · GCP ·
Terraform) drive automatic best-practice review. **Templates** are Backstage
Software Template-compatible and carry an embedded `policy:` block — they
scaffold *and* enforce their rules. A drift watcher proposes template updates via
pull request as the clouds change.

## Command-execution safety

Tool calls that run shell commands pass a **command allowlist** before execution.
The allowlist is parsed from the real shell grammar (`bashlex`), not a regex — it
walks the AST and surfaces every nested command, including those inside `$(...)`,
backticks, pipes, and subshells, so a sanctioned outer command can't smuggle an
unsanctioned inner one (e.g. an IMDS/secret-exfil `curl`). Setting
`PFACTORY_STRICT_COMMAND_PARSING=1` fails closed on input the parser can't
understand. This is defense-in-depth alongside the OS sandbox: the allowlist decides
what is *allowed* to run; the sandbox contains *how* it runs.

The exposure surface is fail-closed by default: `/mcp` returns 401 when its secret is
unset outside dev, a CORS guard rejects wildcard origins with credentials, and the
server refuses to boot with `DISABLE_AUTH` on a non-loopback host. Opt-outs are
explicit and scoped to dev/CI.

## Where it sits

PFactory sits **downstream** of spec-authoring tools (GitHub Spec-Kit, AWS Kiro,
BMAD-METHOD) — it *consumes* their spec rather than competing with them; they stop
at text, PFactory grounds and governs it — and **upstream** of any execution agent.
Within the suite it **plans & governs**,
[AIFactory](https://github.com/olafkfreund/AIFactory) **executes** the emitted
issues, and TFactory **tests** the result — `plan → build → test`, governed end to
end. The emitted GitHub issues are agent-agnostic: AIFactory, the Copilot coding
agent, Devin, or a human can pick them up.

The full design spec lives in
[`docs/plans/2026-06-03-pfactory-design.md`](https://github.com/olafkfreund/PFactory/blob/main/docs/plans/2026-06-03-pfactory-design.md).
