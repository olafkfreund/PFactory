# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Product identity (DEC-006, 2026-06-04):** PFactory is the **feasibility &
governance gate between an AI plan and execution, grounded in the customer's live
cloud.** It ingests a plan (including the output of GitHub Spec-Kit / AWS Kiro /
BMAD-METHOD), enriches it with read-only live-cloud context, runs pre-code
feasibility (cost · IAM access · quotas) + architecture/security/best-practice
review gates, requires one human approval, and emits governed, tagged GitHub epics
+ child issues any execution agent can build. It sits *downstream* of
spec-authoring tools and *upstream* of any execution agent. See
`.agent-os/product/mission.md` and `decisions.md` (DEC-006).

The shipped **planning engine** is the product (`plan/`-stage pipeline: ingest →
enrich → detect → decompose → synthesize → feasibility → review-gates → annotate →
board → approval → emit).

**Test-generation pipeline (demoted to a feature per DEC-006).** A large part of
this codebase is still the inherited autonomous test-generation pipeline: it
receives a finished AIFactory feature on a branch, generates pytest/Jest/Playwright
tests aligned to the acceptance criteria, runs them in a Docker sandbox, evaluates
quality via a 5-signal verdict pipeline (coverage delta · 3× stability · mutate-and-
check · flake-lint promotion · LLM semantic relevance), and emits a triage report.
This is accurate, real, and documented below — but it is now a *feature* (belongs
in the test factory), not PFactory's product identity. Most of the architecture
sections in this file describe that pipeline.

**Status:** v0.2.0 released (2026-05-29) — 16 of 16 v0.2 tasks delivered.
Modality-based lane spine active: unit / browser / api / integration / mutation
(pytest · Jest · Playwright). Application security scanning (SAST/DAST of the
code under test) is out of scope — delegated to dedicated pipelines, not
generated here.

**Cloud infrastructure testing (epic #133, complete):** a separate read-only
assessment flow — access gate → discovery → topology diagram → Prowler/CIS
misconfiguration scan (OCSF) → accept/flag/reject verdict → remediation plan —
for **AWS, GCP and Azure** (all live-verified). This is cloud *posture* (CSPM),
distinct from the app-code SAST/DAST above. Launchable from the portal
(+Task → Cloud Infrastructure); see `guides/cloud-testing.md`. Also wired this
cycle: Kubernetes port-forward dispatch (#108), test-target login credentials
into the lanes (#107), and visual-regression baselines in the portal (#109).

**GitHub Agentic Integration (epic #87, complete):** four opt-in, additive
components that plug PFactory into GitHub's native agentic surface — (1) a
**GitHub Models provider** (`github-models/<publisher>/<model>` → free
`models.github.ai` inference authed by `GITHUB_TOKEN`, routed through the
openai-compatible backend); (2) **Copilot cloud-agent dispatch**
(`copilot:delegate` → `copilot-swe-agent[bot]` drafts a plan PR, gated by
`PFACTORY_COPILOT_DISPATCH_ENABLED`); (3) an **HTTP planning-context MCP server**
at `POST /mcp` (`apps/web-server/server/routes/mcp_rpc.py`) exposing
`pfactory_get_{epic,requirements,decomposition,task_contract,review_status}` —
the runtime bridge for RFC-0002 Task Contract v2, Bearer `PFACTORY_MCP_SECRET`;
(4) two **GitHub Actions** (`pfactory-task.yml`, `copilot-plan-review.yml`) plus
the `/api/github/prs/{pr}/plan-review` endpoint (comment opt-in via
`PFACTORY_PLAN_REVIEW_COMMENT`). See `guides/github-agentic-integration.md`.

**Project:** PFactory
**Repository:** https://github.com/olafkfreund/PFactory
**Author:** DataSeek Team
**License:** MIT OR GPL-3.0

### Product source-of-truth (`.agent-os/product/`)

- **Mission & positioning:** `.agent-os/product/mission.md`
- **Roadmap (3 horizons + shipped):** `.agent-os/product/roadmap.md`
- **Decision log (highest override priority):** `.agent-os/product/decisions.md`
- **GTM & pricing model:** `.agent-os/product/pricing.md`

Active work is tracked under epic [#33](https://github.com/olafkfreund/PFactory/issues/33).

### Pipeline architecture (the 4 agents)

```
  Planner ─► Gen-Functional ─► Executor ─► Evaluator ─► Triager
   (#6)        (#7)              (#5)        (#8)        (#9)
```

Each agent lives under `apps/backend/agents/`:
  - `planner.py`         — emits lane-tagged `test_plan.json`
  - `gen_functional.py`  — writes pytest files per subtask
  - (Executor — `tools/runners/docker_runner.py`, no LLM)
  - `evaluator.py`       — 5-signal verdicts → `findings/verdicts.json`
  - `triager.py`         — dedup + rank → `findings/triage_report.{md,json}`
                           + git_writer + pr_comment (dry-run by default)

Pipeline supporting primitives (also under `agents/`):
  - `preflight_static.py` + `flake_risk_lint.py` — Gen-Functional guards
  - `coverage_delta.py` + `stability_runner.py` + `mutate_probe.py`
    + `lint_promotion.py` — Evaluator's four pre-computed signals
  - `mutation_dispatch.py` — per-language mutation routing (#41): Python
    (`mutate_probe`) vs TypeScript (`lang_typescript/mutate_probe`, Stryker);
    the Evaluator dispatches by `subtask.language` (Java/PIT = future)
  - `flaky_history.py` — cross-run flip-rate history (#37): persists each
    test's pass/fail across runs (`<workspace>/<project>/test_history.json`)
    so chronically flaky tests are flagged even when one run's 3× stability
    happens to pass
  - `triage_dedup.py` + `triage_report.py` — Triager primitives

Each agent has its own `prompts/<agent>.md` system prompt + an assembly
helper in `prompts_pkg/prompts.py`. SDK seams (`_resolve_*_client`,
`_invoke_session`) are mockable in tests — the suite never hits a real LLM.

**Auto-fire chain:** every stage's success path calls
`schedule_<next>(spec_dir, project_dir)` which fires the next agent
asynchronously, gated by env (`PFACTORY_AUTO_PLAN`,
`PFACTORY_AUTO_GENERATE`, `PFACTORY_AUTO_EVALUATE`, `PFACTORY_AUTO_TRIAGE`).
Default ON in production; tests pin OFF.

**Triager side-effects** (git commit + PR comment) default to **DRY-RUN**
per the "no automatic pushes" policy. Operators opt in via:
  - `PFACTORY_TRIAGER_GIT_WRITE=1`
  - `PFACTORY_TRIAGER_PR_COMMENT=1`

**Triager completion callback (#85)** — when the task reaches a terminal
status (`triaged` / `triaged_empty` / `triager_failed`), an opt-in callback
fires so `/pfactory-watch` needs no polling. Both default OFF and are
best-effort (a failing target never breaks the pipeline):
  - `PFACTORY_COMPLETION_WEBHOOK=<url>` — POSTs `{task_id, project_id, status,
    phase, updated_at}` (timeout `PFACTORY_COMPLETION_WEBHOOK_TIMEOUT`, default 5s)
  - `PFACTORY_COMPLETION_SENTINEL=1` — writes `findings/COMPLETED.json` a
    same-host watcher can stat instead of polling

**Bidirectional AIFactory ↔ PFactory bridge (epic #182)** — the reverse of the
handover: when a run finishes with failing tests, PFactory packages a
**correction** (`agents/handback/` — builder + renderer + dry-run-first sender)
and hands it back to AIFactory, whose receiver writes `QA_FIX_REQUEST.md` onto
the original spec and runs the QA Fixer. The Triager's terminal-status hook
*prepares* the artifact (`findings/handback_request.{md,json}`); the operator
sends it via `/handback-to-aifactory` (or `python -m agents.handback <spec_dir>
--send`); `/pfactory-fixloop` drives a bounded test→fix→re-test loop. All
side-effects honor the no-automatic-pushes policy. Env (all opt-in / tunable):
  - `PFACTORY_HANDBACK_PREPARE` (default ON) — build + write the artifact
  - `PFACTORY_HANDBACK_SEND=1` (default OFF) — also POST to AIFactory
  - `PFACTORY_AIFACTORY_API_URL` (default `http://localhost:3101`) — AIFactory web-server
  - `PFACTORY_HANDBACK_MAX_CYCLES` (default 2) — correction-cycle cap → `stuck`

  The AIFactory-side receiver lives in that repo (issue `AIFactory#317`). See
  `guides/aifactory-handback.md`.

**LLM provider abstraction:** PFactory uses the Claude Agent SDK
(`claude-agent-sdk`) as its primary provider, but also supports Codex CLI,
GitHub Copilot CLI, Gemini CLI, Ollama, and any OpenAI-compatible endpoint
(LM Studio, vLLM, OpenRouter, Together, Groq, LocalAI) via the provider
factory in `apps/backend/providers/`. Provider selection is driven by the
model string — see `phase_config.infer_provider_from_model()`. Never call
`anthropic.Anthropic()` directly; route Claude interactions through
`core.client.create_client()` and other providers through
`providers.factory.get_provider()`.

Provider notes worth knowing:
  - **Codex** (`gpt-*` / `*codex*` → `codex_agentic.py`, `codex mcp-server`):
    when `OPENAI_API_KEY` is set, the provider writes an api-key `auth.json`
    into a PFactory-owned `CODEX_HOME` (`~/.pfactory/codex-home/`) and points
    the subprocess at it — so Codex works regardless of the global
    `codex login` state (a bare ChatGPT-account login rejects every model with
    "model is not supported when using Codex with a ChatGPT account"). Your
    global `codex login` is left untouched. With no key, it falls back to the
    inherited login (a Codex-enabled ChatGPT plan still works).
  - **GitHub Copilot CLI** (`copilot:<model>` → `copilot_agentic.py`): runs
    `copilot -p "<prompt>" --allow-all-tools --model <model>` headlessly;
    Copilot does its own tool loop in one shot. Models: `claude-sonnet-4.5`
    (default), `claude-sonnet-4`, `gpt-5` — billed to your Copilot
    subscription. Requires `copilot` installed + signed in.

**BYO-LLM / air-gapped (#38):** `byo_llm.py` classifies a model+endpoint's
data-egress posture (LOCAL / SELF_HOSTED / MANAGED_CLOUD) so the portal/CLI
can show an honest "🔒 Local — no data egress" badge. `python apps/backend/
byo_llm.py <model>` exits 0 only when the run keeps all data on your network.
See `guides/byo-llm.md`.

**Generic AC sources / no-AIFactory (#40):** `spec_sources.py` ingests any
acceptance-criteria source (markdown / Gherkin `.feature` / EARS) and
normalises it into the canonical `context/aifactory_spec.md` the Planner
reads — so PFactory runs without AIFactory. `write_spec_markdown()` is the
pipeline seam; `python apps/backend/spec_sources.py <file>` is the CLI. See
`guides/spec-sources.md`.

### Workspace layout

Per-task data lives at `~/.pfactory/workspaces/<project_id>/specs/<spec_id>/`:

```
status.json                          ← live status (status / phase / counts)
test_plan.json                       ← Planner's lane-tagged subtask plan
context/
  aifactory_spec.md                  ← frozen snapshot of the AIFactory spec
  aifactory_plan.json                ← AIFactory's implementation plan (if any)
  diff.patch                         ← base_ref..branch diff
  source.json                        ← branch + base_ref + repo metadata
  replan_request.json                ← written by Gen-Functional on guardrail reject
tests/                                ← generated pytest files (Gen-Functional)
findings/
  verdicts.json                      ← Evaluator's per-test verdicts
  triage_report.{md,json}            ← Triager's renderable report
  pr_comment_body.md                 ← PR comment body (when no PR# in source.json)
  mutants/                           ← mutate_probe.py's per-test mutants
logs/
  planner.log + gen_functional.log + evaluator.log + triager.log
```

Override workspace root with `PFACTORY_WORKSPACE_ROOT=/path/to/dir`.

## Project Structure

```
Claude-Code-Manager-Web/
├── apps/
│   ├── backend/           # Python backend/CLI - ALL agent logic lives here
│   │   ├── core/          # Client, auth, security
│   │   ├── agents/        # Agent implementations
│   │   ├── spec_agents/   # Spec creation agents
│   │   ├── integrations/  # Graphiti, Linear, GitHub
│   │   └── prompts/       # Agent system prompts
│   ├── web-server/        # FastAPI backend for web UI (REST/WebSocket)
│   └── frontend-web/      # React web frontend (browser-based)
├── guides/                # Documentation
├── tests/                 # Test suite
└── scripts/               # Build and utility scripts
```

**When working with AI/LLM code:**
- Look in `apps/backend/core/client.py` for the Claude SDK client setup
- Reference `apps/backend/agents/` for working agent implementations
- Check `apps/backend/spec_agents/` for spec creation agent examples
- NEVER use `anthropic.Anthropic()` directly - always use `create_client()` from `core.client`

**Frontend (Web):**
- Built with React 19, TypeScript, Vite
- Browser-based UI accessible from any device
- Real-time updates via WebSocket

## Commands

### Setup

**Requirements:**
- Python 3.12+ (required for backend)
- Node.js (for frontend)

```bash
# Install all dependencies from root
npm run install:all

# Or install separately:
# Backend (from apps/backend/)
cd apps/backend && uv venv && uv pip install -r requirements.txt

# Frontend (from apps/frontend/)
cd apps/frontend && npm install

# Set up OAuth token
claude setup-token
# Add to apps/backend/.env: CLAUDE_CODE_OAUTH_TOKEN=your-token
```

### Creating and Running Specs
```bash
cd apps/backend

# Create a spec interactively
python spec_runner.py --interactive

# Create spec from task description
python spec_runner.py --task "Add user authentication"

# Force complexity level (simple/standard/complex)
python spec_runner.py --task "Fix button" --complexity simple

# Run autonomous build
python run.py --spec 001

# List all specs
python run.py --list
```

### Workspace Management
```bash
cd apps/backend

# Review changes in isolated worktree
python run.py --spec 001 --review

# Merge completed build into project
python run.py --spec 001 --merge

# Discard build
python run.py --spec 001 --discard
```

### QA Validation
```bash
cd apps/backend

# Run QA manually
python run.py --spec 001 --qa

# Check QA status
python run.py --spec 001 --qa-status
```

### Testing
```bash
# Install test dependencies (required first time)
cd apps/backend && uv pip install -r ../../tests/requirements-test.txt

# Run all tests (use virtual environment pytest)
apps/backend/.venv/bin/pytest tests/ -v

# Run single test file
apps/backend/.venv/bin/pytest tests/test_security.py -v

# Run specific test
apps/backend/.venv/bin/pytest tests/test_security.py::test_bash_command_validation -v

# Skip slow tests
apps/backend/.venv/bin/pytest tests/ -m "not slow"

# Or from root
npm run test:backend
```

### Spec Validation
```bash
python apps/backend/validate_spec.py --spec-dir apps/backend/specs/001-feature --checkpoint all
```

### Releases
```bash
# 1. Bump version on your branch (creates commit, no tag)
node scripts/bump-version.js patch   # 2.8.0 -> 2.8.1
node scripts/bump-version.js minor   # 2.8.0 -> 2.9.0
node scripts/bump-version.js major   # 2.8.0 -> 3.0.0

# 2. Push and create PR to main
git push origin your-branch
gh pr create --base main

# 3. Merge PR → GitHub Actions automatically:
#    - Creates tag
#    - Builds all platforms
#    - Creates release with changelog
#    - Updates README
```

See [RELEASE.md](RELEASE.md) for detailed release process documentation.

## Architecture

### Core Pipeline (the 4 agents)

```
  Planner ─► Gen-Functional ─► Executor ─► Evaluator ─► Triager
```

Each agent has a stub commit + 4-5 commits of real implementation +
an integration test commit. Total per agent: 6 commits. Pattern shipped
across Tasks 5-8 (Planner / Gen-Functional / Evaluator / Triager) and
Task 4 for the Executor.

**Planner** (`agents/planner.py`):
  - Reads `context/aifactory_spec.md` + `context/diff.patch`
  - Emits `test_plan.json` (lane-tagged subtasks across the v0.2 spine, one phase per AC)
  - Initial + replan modes; `replan_count >= 2` → status=stuck
  - Hard cap: 30 subtasks; soft warning at 15

**Gen-Functional** (`agents/gen_functional.py`):
  - Per-subtask loop: prompt → SDK → write test file
  - Two guardrails per subtask:
    - `preflight_static.py` — subprocess-checks every import resolves
    - `flake_risk_lint.py` — AST scan for 5 flake patterns
      (dict-iter-order, set-iter-order, random-no-seed = high reject;
       time.sleep, datetime.now-no-freeze = medium flag)
  - Rejection → writes `context/replan_request.json` → triggers Planner replan
  - Status: generating → generated / generated_empty / replan_needed / gen_functional_failed

**Executor** (`tools/runners/docker_runner.py`, no LLM):
  - DockerRunner.run_pytest in `--network=none --read-only` container
  - Emits coverage.xml + junit.xml to scratch volume
  - The Evaluator's runner_fn seam wraps this; tests pass canned exit codes

**Evaluator** (`agents/evaluator.py`) — *structurally separate from
Gen-Functional, research-mandated for non-self-validation*:
  - Builds an `EvaluatorSignals` bundle per completed test
  - 5 signals (4 pre-computed in code, 1 LLM-judged):
    1. coverage_delta (`coverage_delta.py` — Cobertura XML parser + set math)
    2. stability (`stability_runner.py` — 3× re-run via runner_fn)
    3. mutation (`mutate_probe.py` — AST mutates ONE assertion; KILLED/SURVIVED)
    4. lint_promotion (`lint_promotion.py` — promotes flake-lint mediums)
    5. semantic_relevance (the LLM's call — high/medium/low)
  - Hands the bundle to evaluator.md prompt; LLM emits `findings/verdicts.json`
  - Validates the verdict JSON (test_id presence + verdict ∈ {accept,reject,flag})

**Triager** (`agents/triager.py`):
  - Loads verdicts.json; wraps as TriageCandidates
  - Drops rejects; dedups byte-identical + whitespace-normalised
    via `triage_dedup.py`
  - Ranks by (verdict_priority, mutation, stability, coverage_delta, test_id)
  - Renders `findings/triage_report.{md,json}` via `triage_report.py`
  - Side-effects (DRY-RUN by default):
    - `tools/git_writer.py` — commits to AIFactory feature branch
    - `tools/pr_comment.py` — `gh pr comment --body-file -`
  - Final status: triaged / triaged_empty / triager_failed

### Key Components (apps/backend/)

**Core Infrastructure:**
- **core/client.py** - Claude Agent SDK client factory with security hooks and tool permissions
- **core/security.py** - Dynamic command allowlisting based on detected project stack
- **core/auth.py** - OAuth token management for Claude SDK authentication
- **agents/** - The 4 PFactory agents + their primitives:
  - `planner.py` / `gen_functional.py` / `evaluator.py` / `triager.py`
  - `preflight_static.py` + `flake_risk_lint.py` (Gen-Functional guardrails)
  - `coverage_delta.py` + `stability_runner.py` + `mutate_probe.py`
    + `lint_promotion.py` (Evaluator's 4 numeric signals)
  - `triage_dedup.py` + `triage_report.py` (Triager primitives)
- **tools/** - Side-effect helpers:
  - `git_writer.py` — commits to AIFactory branch (dry-run-first)
  - `pr_comment.py` — `gh pr comment` with body via stdin (dry-run-first)
  - `runners/docker_runner.py` — sandboxed pytest execution
- **prompts/** + **prompts_pkg/** - System prompts + duck-typed assembly helpers
- **workspaces/snapshotter.py** - Task 3's spec snapshotter
- **mcp_server/pfactory_server.py** - MCP server exposing `task_create_and_run` etc.

**Memory & Context:**
- **integrations/graphiti/** - Graphiti memory system (mandatory)
  - `queries_pkg/graphiti.py` - Main GraphitiMemory class
  - `queries_pkg/client.py` - LadybugDB client wrapper
  - `queries_pkg/queries.py` - Graph query operations
  - `queries_pkg/search.py` - Semantic search logic
  - `queries_pkg/schema.py` - Graph schema definitions
- **graphiti_config.py** - Configuration and validation for Graphiti integration
- **graphiti_providers.py** - Multi-provider factory (OpenAI, Anthropic, Azure, Ollama, Google AI)
- **agents/memory_manager.py** - Session memory orchestration

**Workspace & Security:**
- **cli/worktree.py** - Git worktree isolation for safe feature development
- **context/project_analyzer.py** - Project stack detection for dynamic tooling
- **auto_claude_tools.py** - Custom MCP tools integration

**BMad Method Integration:**
- **integrations/bmad/** - BMad Method scale-adaptive intelligence
  - `complexity_detector.py` - 5-level complexity detection (0-4)
  - `track_config.py` - Three-track planning (Quick Flow, Standard, Enterprise)
  - `session_config.py` - Session segmentation configuration
  - `context_shard.py` - Context sharding for token optimization
  - `session_spawner.py` - Per-story session orchestration
  - `personas/` - BMad-style agent personas (Sarah, Winston, Alex, Jordan)
  - `subagents/` - Specialized analysis sub-agents
    - `requirements_analyst.py` - Requirements validation
    - `codebase_analyzer.py` - Codebase exploration
    - `technical_evaluator.py` - Technical decision evaluation

**Integrations:**
- **linear_updater.py** - Optional Linear integration for progress tracking
- **runners/github/** - GitHub Issues & PRs automation

### Agent Prompts (apps/backend/prompts/)

The 4 PFactory agents each have a single system prompt; assembly helpers
in `prompts_pkg/prompts.py` prepend a per-call CONTEXT block.

| Prompt | Purpose | Helper |
|--------|---------|--------|
| planner.md | Initial-mode plan from AIFactory spec | `get_pfactory_planner_prompt` |
| planner_replan.md | Replan after Gen-Functional rejection | `get_pfactory_planner_replan_prompt` |
| gen_functional.md | Generate one pytest file per subtask | `get_pfactory_gen_functional_prompt` |
| evaluator.md | Verdicts from 4 signals + LLM's semantic call | `get_pfactory_evaluator_prompt` |

The Triager has no LLM prompt — it's a pure-compute orchestrator over
the dedup + rank + render primitives.

### Spec Directory Structure

Each spec in `.pfactory/specs/XXX-name/` contains:
- `spec.md` - Feature specification
- `requirements.json` - Structured user requirements
- `context.json` - Discovered codebase context
- `test_plan.json` - Subtask-based plan with status tracking
- `qa_report.md` - QA validation results
- `QA_FIX_REQUEST.md` - Issues to fix (when rejected)

### Branching & Worktree Strategy

PFactory uses git worktrees for isolated builds. All branches stay LOCAL until user explicitly pushes:

```
main (user's branch)
└── auto-claude/{spec-name}  ← spec branch (isolated worktree)
```

**Key principles:**
- ONE branch per spec (`auto-claude/{spec-name}`)
- Parallel work uses subagents (agent decides when to spawn)
- NO automatic pushes to GitHub - user controls when to push
- User reviews in spec worktree (`.worktrees/{spec-name}/`)
- Final merge: spec branch → main (after user approval)

**Workflow:**
1. Build runs in isolated worktree on spec branch
2. Agent implements subtasks (can spawn subagents for parallel work)
3. User tests feature in `.worktrees/{spec-name}/`
4. User runs `--merge` to add to their project
5. User pushes to remote when ready

### Contributing

**Branching model:** `dev` is the working branch — that's where feature
work and PRs go. `main` is a release branch that only receives
promotion merges from `dev` (the `release:` commits you see on `main`).
Do NOT branch new feature work from `main` — always branch from
`origin/dev`.

**Workflow for contributions:**
1. Fetch and branch from `dev`: `git fetch origin && git checkout -b feat/my-feature origin/dev`
2. Make changes and commit with sign-off: `git commit -s -m "feat: description"`
3. Push to your branch: `git push -u origin feat/my-feature`
4. Create PR targeting `dev`: `gh pr create --base dev`

**Verify before PR:**
```bash
# Ensure only your commits are included
git log --oneline origin/dev..HEAD
```

### Security Model

Three-layer defense:
1. **OS Sandbox** - Bash command isolation
2. **Filesystem Permissions** - Operations restricted to project directory
3. **Command Allowlist** - Dynamic allowlist from project analysis (security.py + project_analyzer.py)

Security profile cached in `.pfactory-security.json`.

### Claude Agent SDK Integration

**CRITICAL: PFactory uses the Claude Agent SDK for ALL AI interactions. Never use the Anthropic API directly.**

**Client Location:** `apps/backend/core/client.py`

The `create_client()` function creates a configured `ClaudeSDKClient` instance with:
- Multi-layered security (sandbox, permissions, security hooks)
- Agent-specific tool permissions (planner, coder, qa_reviewer, qa_fixer)
- Dynamic MCP server integration based on project capabilities
- Extended thinking token budget control

**Example usage in agents:**
```python
from core.client import create_client

# Create SDK client (NOT raw Anthropic API client)
client = create_client(
    project_dir=project_dir,
    spec_dir=spec_dir,
    model="claude-sonnet-4-5-20250929",
    agent_type="coder",
    max_thinking_tokens=None  # or 5000/10000/16000
)

# Run agent session
response = client.create_agent_session(
    name="coder-agent-session",
    starting_message="Implement the authentication feature"
)
```

**Why use the SDK:**
- Pre-configured security (sandbox, allowlists, hooks)
- Automatic MCP server integration (Context7, Linear, Graphiti, Puppeteer)
- Tool permissions based on agent role
- Session management and recovery
- Unified API across all agent types

**Where to find working examples:**
- `apps/backend/agents/planner.py` - Planner agent
- `apps/backend/agents/coder.py` - Coder agent
- `apps/backend/agents/qa_reviewer.py` - QA reviewer
- `apps/backend/agents/qa_fixer.py` - QA fixer
- `apps/backend/spec_agents/` - Spec creation agents

### Memory System

**Graphiti Memory (Mandatory)** - `integrations/graphiti/`

PFactory uses Graphiti as its primary memory system with embedded LadybugDB (no Docker required):

- **Graph database with semantic search** - Knowledge graph for cross-session context
- **Session insights** - Patterns, gotchas, discoveries automatically extracted
- **Multi-provider support:**
  - LLM: OpenAI, Anthropic, Azure OpenAI, Ollama, Google AI (Gemini)
  - Embedders: OpenAI, Voyage AI, Azure OpenAI, Ollama, Google AI
- **Modular architecture:** (`integrations/graphiti/queries_pkg/`)
  - `graphiti.py` - Main GraphitiMemory class
  - `client.py` - LadybugDB client wrapper
  - `queries.py` - Graph query operations
  - `search.py` - Semantic search logic
  - `schema.py` - Graph schema definitions

**Configuration:**
- Set provider credentials in `apps/backend/.env` (see `.env.example`)
- Required env vars: `GRAPHITI_ENABLED=true`, `ANTHROPIC_API_KEY` or other provider keys
- Memory data stored in `.pfactory/specs/XXX/graphiti/`

**Usage in agents:**
```python
from integrations.graphiti.memory import get_graphiti_memory

memory = get_graphiti_memory(spec_dir, project_dir)
context = memory.get_context_for_session("Implementing feature X")
memory.add_session_insight("Pattern: use React hooks for state")
```

### BMad Method Integration

**Status:** ✅ Complete (Branch: `bmad-method`, awaiting PR to `develop`)

PFactory integrates BMad Method's scale-adaptive intelligence, specialized agents, and architecture-first workflows. All 6 milestones implemented.

#### Key Features

1. **5-Level Complexity Detection** (`integrations/bmad/complexity_detector.py`)
   - Level 0: Trivial (single atomic change, 1 story)
   - Level 1: Simple (small feature, 1-10 stories)
   - Level 2: Standard (medium project, 5-15 stories)
   - Level 3: Complex (complex system, 12-40 stories, architecture required)
   - Level 4: Enterprise (40+ stories, full planning)
   - Detection: > 80% accuracy using keyword + story count estimation

2. **Three-Track Planning** (`integrations/bmad/track_config.py`)
   - **Quick Flow** (3 phases): Discovery → Tech Spec → Validate
   - **Standard** (6-7 phases): Adds Requirements, Context, Spec, Plan (+ Architecture for Level 3+)
   - **Enterprise** (9 phases): Adds Security, DevOps phases

3. **Architecture-First Planning** (`agents/architect.py`)
   - Architecture phase between requirements and implementation (Level 3+)
   - Database schema design (ERD)
   - API endpoint specifications (OpenAPI)
   - Technical decision records (ADRs)
   - Mermaid diagram generation (ERD, C4, sequence)

4. **Story-Based Planning** (`agents/planner.py`)
   - User stories with acceptance criteria (not generic subtasks)
   - Technical context (architecture references, stack, dependencies)
   - Story points and priority
   - Backward compatible with old subtask format

5. **Agent Personas** (`integrations/bmad/personas/`)
   - **Sarah (Planner)**: Staff PM, ruthlessly prioritizes
   - **Winston (Architect)**: Principal Architect, security-conscious
   - **Alex (Coder)**: Senior Developer, ultra-succinct
   - **Jordan (QA)**: Lead QA Engineer, detail-oriented

6. **Session Segmentation** (`integrations/bmad/session_*.py`)
   - Per-story sessions with minimal context
   - Context sharding (loads only relevant architecture sections)
   - **98.9% token reduction** (50,000 chars → 529 chars avg)
   - Opt-in via settings UI or `BMAD_SESSION_SEGMENTATION` env var

7. **Sub-Agents Framework** (`integrations/bmad/subagents/`)
   - **RequirementsAnalyst**: Validates requirements (80% completeness, 100% clarity)
   - **CodebaseAnalyzer**: Explores structure, detects tech stack, finds relevant files
   - **TechnicalEvaluator**: Assesses risks, evaluates decisions, recommends best practices

#### Using BMad Method

**Enable Session Segmentation:**
```bash
# Option 1: Environment variable
export BMAD_SESSION_SEGMENTATION=true

# Option 2: Frontend UI toggle
# Settings > Agent Settings > BMad Session Segmentation
```

**Using Sub-Agents:**
```python
from integrations.bmad.subagents.examples import SubAgentInvoker

invoker = SubAgentInvoker(project_dir, spec_dir)

# Before planning
req_analysis = invoker.analyze_requirements(requirements_text)

# Before implementation
code_analysis = invoker.find_relevant_files(task_description)

# For architecture decisions
eval_result = invoker.evaluate_decision(decision_text)
```

**Documentation:**
- **Implementation Summary:** `_bmad/IMPLEMENTATION_SUMMARY.md`
- **Sub-Agents Guide:** `apps/backend/integrations/bmad/subagents/README.md`
- **Planning Documents:** `_bmad/plan.md`, `prd.md`, `architecture.md`, `epics.md`

**Testing:**
```bash
# Test sub-agents
cd apps/backend
python3 -m integrations.bmad.subagents.test_subagents

# Run examples
python3 -m integrations.bmad.subagents.examples
```

## Development Guidelines

### Frontend Internationalization (i18n)

**CRITICAL: Always use i18n translation keys for all user-facing text in the frontend.**

The frontend uses `react-i18next` for internationalization. All labels, buttons, messages, and user-facing text MUST use translation keys.

**Translation file locations:**
- `apps/frontend/src/shared/i18n/locales/en/*.json` - English translations
- `apps/frontend/src/shared/i18n/locales/fr/*.json` - French translations

**Translation namespaces:**
- `common.json` - Shared labels, buttons, common terms
- `navigation.json` - Sidebar navigation items, sections
- `settings.json` - Settings page content
- `dialogs.json` - Dialog boxes and modals
- `tasks.json` - Task/spec related content
- `onboarding.json` - Onboarding wizard content
- `welcome.json` - Welcome screen content

**Usage pattern:**
```tsx
import { useTranslation } from 'react-i18next';

// In component
const { t } = useTranslation(['navigation', 'common']);

// Use translation keys, NOT hardcoded strings
<span>{t('navigation:items.githubPRs')}</span>  // ✅ CORRECT
<span>GitHub PRs</span>                          // ❌ WRONG
```

**When adding new UI text:**
1. Add the translation key to ALL language files (at minimum: `en/*.json` and `fr/*.json`)
2. Use `namespace:section.key` format (e.g., `navigation:items.githubPRs`)
3. Never use hardcoded strings in JSX/TSX files

### Temporary Files and Debugging Documentation

**IMPORTANT: NEVER create debugging or analysis files in the project root directory.**

**For temporary debugging files, analysis documents, or troubleshooting guides:**
- Use the system temp directory: `/tmp/`
- Or use the Claude Code scratchpad directory for your session
- These files should NOT be committed to the repository

**Examples of files to keep out of project root:**
- `TASK_*.md` - Task debugging analysis documents
- `INIT_*.md` - Initialization troubleshooting guides
- `DEBUG_*.md` - Debug session notes
- `ANALYSIS_*.md` - Code analysis documents
- Any other temporary troubleshooting documentation

**Where to put debugging documentation:**
```bash
# Option 1: System temp directory
/tmp/task-001-debug-analysis.md

# Option 2: Claude Code scratchpad (session-specific)
# Use the scratchpad directory provided in your session context

# Option 3: If it's permanent documentation, put it in guides/
guides/troubleshooting/specific-issue.md
```

**Exception:** Only add permanent troubleshooting documentation to `guides/` directory if it's meant to be version-controlled and shared with all developers.

## Web Interface

PFactory is a browser-based web interface. This enables:
- Remote access from any device with a browser
- Server-based deployments
- Headless operation with web UI control

### Web Interface Architecture

```
apps/
├── web-server/     # FastAPI backend (Python) - REST API + WebSocket
└── frontend-web/   # React frontend (TypeScript/Vite) - Browser UI
```

### Quick Start (Web Interface)

```bash
# Terminal 1: Start the backend server
cd apps/web-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m server.main
# Note: Token is printed to console and saved to ~/.pfactory/.token

# Terminal 2: Start the frontend dev server
cd apps/frontend-web
npm install
npm run dev
```

Access the web UI at `http://localhost:3115` (or your server IP for remote access).

### Configuration

**Backend (`apps/web-server/.env`):**
```bash
APP_HOST=0.0.0.0      # Listen on all interfaces
APP_PORT=3114          # API server port
APP_DEBUG=true         # Enable Swagger docs at /docs
# APP_API_TOKEN=xxx    # Optional: Set fixed token (auto-generated if not set)
```

**Frontend (`apps/frontend-web/.env`):**
```bash
VITE_API_BASE_URL=/api                    # API base (proxied to backend)
VITE_WS_BASE_URL=ws://your-server:3114    # WebSocket URL for remote deployments
VITE_API_URL=http://localhost:3114        # Backend URL for Vite proxy
```

### Remote Access

For remote deployments:
1. Ensure ports 3114 (backend) and 3115 (frontend dev) are accessible
2. Set `VITE_WS_BASE_URL` to your server's WebSocket URL
3. Access via `http://YOUR_SERVER_IP:3115`

For production, build the frontend (`npm run build`) and serve from `apps/web-server/static/`.

### API Documentation

When `APP_DEBUG=true`:
- Swagger UI: `http://localhost:3114/docs`
- ReDoc: `http://localhost:3114/redoc`

See `apps/web-server/README.md` and `apps/frontend-web/README.md` for detailed documentation.

### Web Interface Troubleshooting

| Issue | Solution |
|-------|----------|
| "Claude Code not installed" | Hard refresh browser (`Ctrl+Shift+R`) |
| UI blocked/frozen | Check browser console for errors, restart servers |
| Can't add projects | Use project discovery dropdown or enter custom path |
| API errors | Verify token in `~/.pfactory/.token` |
| Git Repository Required keeps appearing | Click "Skip for now" or initialize git; state persists in localStorage |
| Usage shows NaN | Backend reads stats from `~/.claude/stats-cache.json` |
| New Task button not working | Ensure TaskCreationWizard is imported in App.tsx |
| Terminal 500 error | Check PTYManager creates sessions with UUID (don't pass `id=None`) |
| File editor 404/TypeError | Backend file routes return raw data, frontend extracts `.entries` or `.content` |
| Themes not changing | Ensure CSS theme variables exist in `index.css` with `@theme` block |
| Folder tree not expanding | FileTree needs `onLoadChildren` prop for lazy loading subdirectories |
| Task creation fails | Add POST endpoint at `/api/projects/{id}/tasks` in projects.py |
| Task start 404 | Mount execution.router at `/api/tasks` prefix (not `/api/execution`) in main.py |
| Task start 422 | Frontend must send `{}` body even when options are undefined (Pydantic needs JSON body for defaults) |
| Terminal resize 422 | Backend endpoint must use Pydantic model for `{ cols, rows }` body, not query params |
| Task stuck with "Stream closed" | Ensure `permission_mode="bypassPermissions"` is set in all `ClaudeAgentOptions` AND auto-claude MCP tools are in permissions allow list (see `APP_TOOLS` in `models.py`) |
| Frontend shows task "stuck" but agent is working | File sync issue - worktree files not syncing to main spec dir. Fixed in agent_service.py with periodic sync every 3 seconds |
| Roadmap/Changelog 500 error | FastAPI `Path` shadowing `pathlib.Path` - use `from pathlib import Path as FilePath` at top of route files |
| Roadmap progress stuck at 0% | Roadmap generation was not implemented - see `roadmap_service.py` for the service pattern |
| Task options not applied (model/thinking) | Frontend saves to `requirements.json["metadata"]` but `phase_config.py` reads `task_metadata.json`. Fixed: `projects.py` now writes `task_metadata.json` on task creation |
| "Failed to parse insights JSON" in logs | Empty SDK response in `insight_extractor.py`. Fixed with empty response validation and brace-matching fallback |

### Web Interface Development Patterns

**API Response Wrapping:**
- Frontend `api-client.ts` wraps all responses in `{ success: true, data: <response> }`
- Backend endpoints should return raw data objects, NOT wrapped in `{success, data}`
- Exception: Error responses should return `{ success: false, error: "message" }`

**React useEffect Dependencies:**
- Avoid using object references (like `selectedProject`) as dependencies - they change on every render
- Use primitive values (like `selectedProjectId`) instead
- Use refs (`useRef`) to access state in effects without triggering re-runs

**localStorage for Persistent UI State:**
- Use localStorage for UI state that should survive page refresh (e.g., skipped dialogs)
- Initialize state with lazy initializer: `useState(() => loadFromStorage())`
- Update localStorage in state setter callback

**File Routes:**
- `/api/files/list?path=...` - List directory by absolute path (returns `{path, entries, parent}`)
- `/api/files/read?path=...` - Read file by absolute path (returns `{path, content, size, modified, language}`)
- `/api/files/discover?base_path=...` - Discover projects in directory

**Theme System (Tailwind v4):**
- CSS theme variables defined in `apps/frontend-web/src/index.css`
- Light mode: `:root { --background: ...; --foreground: ...; }`
- Dark mode: `.dark { --background: ...; }`
- Color themes: `[data-theme="ocean"]`, `[data-theme="forest"]`, etc.
- Tailwind v4 uses `@theme { --color-background: hsl(var(--background)); }` to map CSS vars
- App.tsx applies themes via `document.documentElement.classList.add('dark')` and `setAttribute('data-theme', theme)`

**Lazy Loading File Trees:**
- FileTree component should accept `onLoadChildren: (path: string) => Promise<FileNode[]>`
- Load children on folder expand, not upfront
- Cache loaded children in component state to avoid refetching

**Worktree File Synchronization:**
- Agent writes files to worktree: `.pfactory/worktrees/tasks/{spec-id}/.pfactory/specs/{spec-id}/`
- Frontend reads from main spec: `.pfactory/specs/{spec-id}/`
- `agent_service.py` syncs files every 3 seconds during task execution (`_sync_worktree_files()` method)
- Synced files: `test_plan.json`, `build-progress.txt`, `context.json`, `qa_report.md`, `spec.md`, `requirements.json`
- Final sync occurs when task completes
- Location: `apps/web-server/server/services/agent_service.py:207-303`

**Running Backend Scripts from Web Server:**
- Use service pattern (see `roadmap_service.py` as example)
- Set `PYTHONPATH` to include backend directory for imports to work
- Set `cwd` to backend directory when running scripts
- Use `sys.executable` (web server's Python) which shares dependencies
- Parse stdout for phase patterns to emit WebSocket progress events
- Example phases: `PHASE 1:`, `PHASE 2:`, etc. detected via regex

**Keyboard Shortcuts:**
- New terminal: `Ctrl+Shift+E` (all platforms) - not `Ctrl+T` which conflicts with browser
- Close terminal: `Ctrl+W` / `Cmd+W`

**Paste and Text Handling (Fixed 2026-01-16):**
- **Issue:** Copy-pasting text with images was losing the text content
- **Root cause:** Paste handlers were calling `e.preventDefault()` to process images, blocking browser's natural text paste
- **Fix:** Removed `e.preventDefault()` - let browser handle text naturally while processing images in parallel
- **Files:** `TaskCreationWizard.tsx`, `TaskEditDialog.tsx` - both paste handlers updated
- **Result:** Text-only, image-only, and mixed content pastes all work correctly

**Task Description Truncation (Fixed 2026-01-16):**
- **Issue:** Multi-paragraph task descriptions truncated to 500 characters in UI
- **Root cause:** Backend `load_spec_metadata()` was loading from `spec.md` with `[:500]` limit
- **Fix:** Load description from `requirements.json` first (complete user input), removed character limit
- **File:** `apps/web-server/server/routes/tasks.py:216-243`
- **Result:** Full task descriptions load correctly in all views (list, detail, edit)

## Running the Application

**As a standalone CLI tool**:
```bash
cd apps/backend
python run.py --spec 001
```

**With the Web interface**:
```bash
# Start backend (port 3114)
cd apps/web-server && source .venv/bin/activate && python -m server.main

# Start frontend (port 3115)
cd apps/frontend-web && npm run dev
```

**Project data storage:**
- `.pfactory/specs/` - Per-project data (specs, plans, QA reports, memory) - gitignored
- `~/.pfactory/` - Web interface data (projects, settings, token) - for web UI only
