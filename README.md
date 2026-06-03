<p align="center">
  <img src="docs/assets/logo/pfactory-logo.svg" alt="PFactory — Plan Factory" width="440"/>
</p>

# PFactory — Plan Factory

**The planning & governance incubator that sits in *front* of the AI execution
agents.** The third factory in the suite alongside
[AIFactory](https://github.com/olafkfreund/AIFactory) (executes tasks) and
TFactory (tests them): **PFactory plans them.**

Hand PFactory a project plan — uploaded as **docx / pdf / markdown**, or via the
**MCP control plane**, **CLI**, or a **GitHub issue/discussion**. PFactory:

1. **Ingests** the plan into a normalized model (markdown / Gherkin / EARS / pdf / docx).
2. **Enriches** it with *live* organizational context — internal wikis &
   **Backstage** (catalog, TechDocs, golden-path templates) for policy &
   best-practice, and *read-only* introspection of running
   **Kubernetes / OpenShift / Azure / AWS / GCP** (load, quotas, policies,
   resources) plus **Terraform** and cloud best-practice **MCP servers**.
3. **Decomposes** it into an epic + child issues; for software targets it adds
   task breakdown, a **Testing Strategy**, and a generated **CI/CD** definition.
4. **Reviews** it through mandatory **architecture / security / best-practices /
   feasibility** gates (hybrid deterministic policy-as-code + LLM lenses) against
   pluggable **templates that carry rules**, then a single **human approval** gate.
5. **Emits** governed **GitHub epics + child issues** (the durable source of
   truth) that **AIFactory picks up and executes** — optionally triggered
   directly via its API, with TFactory test-handover hooks.

Everything is **pluggable**: add MCP servers, skills, agents, and
Backstage-compatible templates via a declarative registry. Templates stay current
— PFactory watches the clouds and proposes updates **via pull request**.

> Status: **v0.1 bootstrapping** — forked and rebranded from the TFactory
> skeleton (~80% reuse), planning pipeline under construction. See the design
> spec in [`docs/plans/2026-06-03-pfactory-design.md`](docs/plans/2026-06-03-pfactory-design.md)
> and the build backlog (Epic + child issues) in the repo's Issues tab.
> Market positioning: [`docs/market-positioning.md`](docs/market-positioning.md).

## Quickstart (NixOS / flake-based)

```bash
# One-command dev environment via the flake:
nix develop

# (inside the shell)
pfactory-minimal-venv   # creates apps/backend/.venv with just pytest+pytest-asyncio
pfactory-test           # runs the non-SDK backend suite (~10s)

# For the full backend SDK install (graphiti, claude-agent-sdk, etc.):
bootstrap-venv
```

The dev shell brings in **Python 3.13, Node 22, uv, git, gh, just,
ripgrep, jq, docker-client** plus four shell functions: `bootstrap-venv`,
`pfactory-minimal-venv`, `pfactory-test`, `verify-fork`.

For auto-loading via `direnv`:

```bash
nix profile install nixpkgs#nix-direnv
direnv allow
```

Non-Nix users can fall back to `npm run install:backend` (per the
[Quickstart](https://olafkfreund.github.io/PFactory/) on Pages) — the
Nix path just makes setup deterministic.

> **Note for non-Nix npm users:** the nix devShell sets `NODE_ENV=production`,
> which makes `npm install` skip devDependencies (including vitest). If
> you're inside `nix develop` and running `npm install` in `apps/frontend-web/`,
> first `unset NODE_ENV`. Captured in detail in `guides/e2e-smoke.md`.

## Running the portal

```bash
# Backend (FastAPI on :3102)
cd apps/web-server
source .venv/bin/activate    # if you have a per-app venv
python -m server.main

# Frontend (Vite dev server on :3100)
cd apps/frontend-web
npm install                  # unset NODE_ENV first if inside nix develop
npm run dev
```

Then visit **http://localhost:3100** for the PFactory portal.

The portal exposes a `/pfactory` view powered by the components under
`apps/frontend-web/src/components/pfactory/`:

- **PFactoryTaskList** — workspace list with status badges
- **PFactoryTaskDetail** — tabs for Status / Lanes / Verdicts / Report / Logs
- **LaneStatusGrid** — Unit / Browser / API / Integration / Mutation lane spine
- **PFactoryLogViewer** — WebSocket live tail (one snapshot per connect at MVP)

## End-to-end smoke

Once you have a real AIFactory project + a Claude API key + Docker:

```bash
# List the 9 verification scenarios
scripts/e2e-smoke.sh --list

# Dry-run (no env, no LLM calls) — sanity check the runner itself
scripts/e2e-smoke.sh --dry-run --all

# Real run
export ANTHROPIC_API_KEY=sk-ant-...
export PFACTORY_AIFACTORY_ROOT=$HOME/Source/GitHub/MyApp
export PFACTORY_AIFACTORY_BRANCH=feature/...
scripts/e2e-smoke.sh --all
```

Full walkthrough — including the 3 manual scenarios (mutation,
hallucination guard, docker-down) — in **`guides/e2e-smoke.md`**.

## Tests

| Suite | What | Count | Time |
|---|---|---|---|
| Backend non-SDK (`tests/test_*.py`) | Pure-Python primitives + agent loops with mocked SDK | **531** | ~9s |
| Frontend (`apps/frontend-web/src/**/*.test.tsx`) | vitest + React Testing Library | **112** | ~1.5s |
| End-to-end smoke (`scripts/e2e-smoke.sh`) | Real LLM + Docker + git + gh — **manual** | 9 scenarios | — |

CI runs the first two on every commit; the third is operator-driven.

```bash
# Backend
PYTHONPATH=apps/backend apps/backend/.venv/bin/pytest -q tests/

# Frontend (under nix devShell, unset NODE_ENV first)
cd apps/frontend-web && ../../node_modules/.bin/vitest run

# Fork-hygiene check (every stray AIFactory reference is allowlisted explicitly)
scripts/verify-fork.sh --no-import
```

## Docs

Full project documentation is published as a GitHub Pages site:
**https://olafkfreund.github.io/PFactory/**

Direct links:

- [Design Plan](https://olafkfreund.github.io/PFactory/design-plan/) — full rationale, 10 locked decisions, landscape research, risk register
- [Spec](https://olafkfreund.github.io/PFactory/spec/) — Agent OS spec
- [Technical Spec](https://olafkfreund.github.io/PFactory/technical-spec/) — architecture detail
- [Test Coverage Spec](https://olafkfreund.github.io/PFactory/tests/) — TDD plan
- [Task Breakdown](https://olafkfreund.github.io/PFactory/tasks/) — 12 tasks with dependency graph

In-repo guides (`guides/`):

- **`guides/e2e-smoke.md`** — operator guide for the 9 verification scenarios
- **`guides/planner-manual-smoke.md`** — Planner-only sibling smoke
- **`guides/HANDOVER_WORKFLOW.md`** — how to trigger PFactory from a live Claude Code session
- **`guides/CLAUDE_CODE_MCP_TOOLS.md`** — driving PFactory tasks from the MCP control plane
- **`guides/byo-llm.md`** — run PFactory **fully on your own infrastructure**
  (Ollama / vLLM / LM Studio / LocalAI) with a verifiable no-egress guarantee —
  for GDPR / HIPAA / air-gapped teams. `python apps/backend/byo_llm.py <model>`
  prints the live data-egress posture (🔒 Local / 🏠 Self-hosted / ☁️ Managed)
- **`guides/spec-sources.md`** — use PFactory **without AIFactory**: ingest any
  acceptance-criteria source (markdown / Gherkin `.feature` / EARS) into the
  pipeline via `python apps/backend/spec_sources.py <file>`

## Project tracking

- **Epic + sub-issues:** https://github.com/olafkfreund/PFactory/issues
- **Discussions / questions:** open an issue with the `question` label

## High-level architecture

```
AIFactory finished branch  ─►  /handover-to-pfactory  ─►  PFactory MCP
                                                              │
                                                              ▼
                                                          Planner
                                                              │
                              ┌──────────┬─────────┬──────────┼──────────┐
                              ▼          ▼         ▼          ▼          ▼
                       Gen-Unit  Gen-Browser  Gen-API  Gen-Integration  Gen-Mut
                              └──────────┴────┬────┴──────────┴──────────┘
                                              ▼
                                          Executor  (Docker per task)
                                              ▼
                                          Evaluator  (separate agent)
                                              ▼
                                          Triager   ─►  git commit + PR comment
```

Five pipeline stages (Planner / per-lane Generators / Executor / Evaluator /
Triager), five lanes (unit / browser / api / integration / mutation), Docker
sandbox, spec-aware handover from AIFactory.

The four-stage chain auto-advances via `PFACTORY_AUTO_*` env vars; each
stage writes its outputs to `~/.pfactory/workspaces/{project}/specs/{spec}/`
and forwards via a fire-and-forget scheduler. See `apps/backend/agents/`
for each agent's implementation.

## Status by lane

v0.2 swapped the v0.1 pipeline-stage decomposition for a
**modality-based spine** (Decision 2). Security scanning is delegated to
dedicated pipelines and out of scope here; PFactory focuses on
functional + feature testing.

| Lane | v0.2.0 status | Runtime | Coverage | Evidence |
|---|---|---|---|---|
| **Unit** | ✅ Active | `pfactory-runner-pytest` (Python) · `pfactory-runner-jest` (TypeScript) | line (cobertura / lcov) | — |
| **Browser** | ✅ Active | `pfactory-runner-playwright` + `AppRuntime` (docker-compose + HTTP HEAD health-poll) | `null` (Decision 11 — line coverage doesn't apply when the test drives the browser) | screenshots · video · trace.zip |
| **API** | ✅ Active | per-framework Docker image + HTTP HAR recorder | line where applicable | network.har |
| **Integration** | ✅ Active | per-framework Docker image + `AppRuntime` (multi-service compose) | line where applicable | network.har · service logs |
| **Mutation** | ✅ Active | `mutmut` (Python) / Stryker (TypeScript) — one-mutation-per-run probe inside the Evaluator | per-mutant (killed / survived) | — |

All five lanes shipped with v0.2.0. The Planner picks each subtask's
lane from its `(language, framework)` via the framework registry
(`frameworks/{pytest,jest,playwright}/descriptor.yaml`). New languages
(Go / Rust / Ruby) and additional security-pipeline integrations slot
into this same spine through new `FrameworkDescriptor`s — no lane
additions required.

## Connect to your environment — Credential Broker

Agents often need to reach **real services and cloud environments** (a staging
API, a Kubernetes cluster, a GCP/AWS/Azure project) to plan and run tests — but
secrets must never land in the repo. The **Credential Broker** (epic
[#62](https://github.com/olafkfreund/PFactory/issues/62)) resolves credentials
from a pluggable backend and exposes them to the agents ephemerally:

- **Backends:** Azure Key Vault · AWS Secrets Manager · GCP Secret Manager ·
  HashiCorp Vault · local **sops / age / agenix** · plain env. One ref syntax
  (`vault:path#field`, `gcp-sm://proj/secret`, `sops:file#key`, …); cloud SDKs
  load lazily so an absent package never breaks startup.
- **Ephemeral + redacted:** file credentials (kubeconfig, GCP ADC) are written
  `0600` to a per-task scratch dir and **wiped** when the task ends; resolved
  values are redacted from logs.
- **Honest egress:** off by default — *no* cloud credential is resolved unless
  the project opts in (`.pfactory.yml` `egress.enabled`).
  `python -m pfactory_secrets.cli audit` prints a secret-free manifest of
  exactly what would leave your network.

**Why:** it extends the existing `core/mcp_credentials.py` ambient chain
(K8s / AWS-IRSA / Azure-MI / GCP-ADC) with a vault-fetch head rather than
reinventing auth, and keeps the same honest-egress posture as
[BYO-LLM](guides/byo-llm.md). See [`guides/credentials.md`](guides/credentials.md)
and the [Credentials](https://olafkfreund.github.io/PFactory/credentials/) page.

## Run on any LLM

PFactory routes each pipeline phase to a provider purely from the **model
string** — no separate provider switch. Supported: the **Claude Agent SDK**
(primary), **OpenAI Codex**, **Gemini CLI**, **GitHub Copilot CLI**, **Ollama**
(local), and any **OpenAI-compatible** endpoint (vLLM / LM Studio / OpenRouter /
Together / Groq / LocalAI). This lets a team run on a flat-rate subscription, a
self-hosted model, or fully air-gapped — with an honest **data-egress badge**
(`python apps/backend/byo_llm.py <model>`) so you always know whether a run
keeps data on your network. See [`guides/byo-llm.md`](guides/byo-llm.md).

## License

[MIT OR GPL-3.0](LICENSE).
