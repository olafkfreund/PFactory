---
layout: default
title: Architecture
permalink: /architecture/
nav_order: 7
---

# Architecture

A walk through what's in this repo, where each concern lives, and how
the runtime pieces connect when a PFactory task fires. Everything below
reflects what's actually on `main` as of the last commit — see
[Progress]({{ '/progress/' | relative_url }}) for the live task status.

## v0.2 pipeline spine (5 lanes)

```
AIFactory finished branch  ─►  /handover-to-pfactory  ─►  PFactory MCP
                                                              │
                                                              ▼
                                                         Planner
                                                    (test_plan.json)
                                                              │
                        ┌──────────┬──────────┬──────────────┼──────────────┐
                        ▼          ▼          ▼               ▼              ▼
                    Gen-Unit   Gen-Browser Gen-API      Gen-Integration  Gen-Mutation
                        └──────────┴─────┬──┴──────────────── ┘──────────────┘
                                         ▼
                              Executor (Docker per subtask,
                             .pfactory.yml target addressing,
                              AppRuntime for browser/api)
                                         ▼
                              Evaluator  (5-signal verdicts:
                               coverage · stability · mutation ·
                               lint-promotion · semantic-relevance)
                                         ▼
                              Triager (update-in-place vs create-new
                               via .pfactory/tests-catalog.json)
                                         ▼
                              git commit + PR comment (dry-run default)
```

All five lanes are wired as of v0.2.0 (released 2026-05-29). Lane
dispatch is gated per the `Lane` enum: `Lane.UNIT` runs pytest;
`Lane.BROWSER` runs Playwright wrapped in `AppRuntime` (docker-compose
start → HTTP HEAD health-poll → tear down with `--volumes`);
`Lane.API` and `Lane.INTEGRATION` use the same per-framework Docker
runner image dispatch plus the HTTP HAR recorder from
`agents/evidence/http_recorder.py`; `Lane.MUTATION` shells out to
Stryker for TypeScript or `mutate_probe.py` for Python. Evidence
artefacts (screenshots / video / trace / HAR) are captured per test
under `findings/evidence/<test_id>/`, served by the portal endpoint
and linked from the Triager PR comment.

## v0.2 lane status

| Lane | Phase | Framework examples | Status |
|------|-------|--------------------|--------|
| **Unit**        | **1** | pytest, Jest, vitest | **Active** |
| **Browser**     | **2** | Playwright (chromium/firefox/webkit) | **Active** (AppRuntime) |
| **API**         | **3** | pytest-httpx, supertest, dredd | **Active** |
| **Integration** | **4** | testcontainers-python, testcontainers-node | **Active** |
| **Mutation**    | **5** | mutmut, cosmic-ray, Stryker | **Active** |

The framework descriptor registry (`framework_registry/`) catalogs
80 frameworks across the five lanes; `.pfactory.yml` configures targets
(HTTP / kubernetes / docker_compose / feature_flag) for each lane.
The tests-catalog (`tests_catalog/`) persists cross-run continuity via
`tests-catalog.json` committed alongside generated tests.

## CLI commands (v0.2)

```bash
# Scaffold a new .pfactory.yml + empty tests-catalog.json
python -m cli init
python -m cli init --non-interactive --target-name api \
    --target-type http --base-url https://api.staging.example.com

# Migrate v0.1 workspace tests to the new catalog format
python -m cli migrate v0_1_catalog
python -m cli migrate v0_1_catalog --dry-run
```

## Repository layout (depth 3)

```
PFactory/
├── apps/
│   ├── backend/                  # Python 3.12, Claude Agent SDK
│   │   ├── agents/
│   │   │   ├── memory_manager.py        # Graphiti + file fallback
│   │   │   ├── planner.py               # ← Task 5 will rewrite
│   │   │   ├── session.py
│   │   │   ├── utils.py
│   │   │   └── tools_pkg/
│   │   │       ├── registry.py          # spec-internal tool registry
│   │   │       ├── tools/
│   │   │       │   ├── memory.py        # in-agent: graphiti ops
│   │   │       │   ├── progress.py      # in-agent: status updates
│   │   │       │   ├── qa.py            # in-agent: validation
│   │   │       │   ├── subtask.py       # in-agent: subtask state
│   │   │       │   └── task_control.py  # ★ MVP MCP surface (7 tools)
│   │   │       └── http_client.py
│   │   ├── mcp_server/
│   │   │   └── pfactory_server.py       # stdio MCP entrypoint
│   │   ├── test_plan/                   # ← renamed from implementation_plan
│   │   │   ├── enums.py                 # ★ Lane enum added in Task 3
│   │   │   ├── subtask.py               # ★ .lane field added in Task 3
│   │   │   ├── phase.py
│   │   │   ├── plan.py                  # ImplementationPlan model
│   │   │   ├── story.py
│   │   │   ├── verification.py
│   │   │   └── factories.py
│   │   ├── workspaces/                  # ★ NEW in Task 3
│   │   │   └── snapshotter.py           # AIFactory → PFactory snapshot
│   │   ├── tools/
│   │   │   ├── executor.py              # in-agent tool runner (inherited)
│   │   │   ├── definitions.py
│   │   │   └── runners/                 # ★ NEW in Task 4
│   │   │       ├── docker_runner.py     # sandboxed test exec
│   │   │       ├── lane_dispatch.py     # lane → runner routing
│   │   │       └── lang_registry.py     # per-lang, per-lane tool table
│   │   ├── prompts/                     # ← Tasks 5-8 add new prompts here
│   │   ├── providers/                   # OpenAI / Anthropic / Ollama factory
│   │   ├── context/                     # project_analyzer (reused)
│   │   ├── memory/, core/, integrations/, runners/
│   ├── web-server/                      # FastAPI — Task 9 retheme
│   └── frontend-web/                    # React — Task 10 retheme
├── docker/
│   └── runners/
│       └── python.Dockerfile            # ★ NEW in Task 4
├── .claude/
│   └── skills/
│       └── handover-to-pfactory/        # ★ NEW in Task 2
│           └── SKILL.md
├── companion-skills/                    # ★ NEW in Task 2
│   └── aifactory-handover-to-pfactory/
│       └── SKILL.md                     # installs into AIFactory
├── docs/                                # Jekyll source for this site
│   ├── _config.yml
│   ├── index.md
│   ├── architecture.md                  # ← you are here
│   ├── progress.md
│   ├── design-plan.md
│   ├── spec.md
│   ├── technical-spec.md
│   ├── tests.md
│   └── tasks.md
├── .agent-os/
│   └── specs/2026-05-28-pfactory-mvp-walking-skeleton/
│       ├── spec.md, tasks.md
│       └── sub-specs/{technical-spec.md, tests.md}
├── scripts/
│   ├── verify-fork.sh                   # ★ NEW in Task 1
│   ├── start-pfactory-mcp.sh            # renamed from start-aifactory-mcp.sh
│   └── ... (other inherited scripts)
├── tests/
│   ├── test_pfactory_mcp_tools.py       # ★ Task 2 (21 cases)
│   ├── test_test_plan_lane.py           # ★ Task 3 (10 cases)
│   ├── test_snapshotter.py              # ★ Task 3 (11 cases)
│   ├── test_docker_runner.py            # ★ Task 4 (28 cases)
│   ├── test_lang_registry.py            # ★ Task 4 (10 cases)
│   ├── test_lane_dispatch.py            # ★ Task 4 (10 cases)
│   └── ... (inherited; some quarantined)
└── charts/pfactory/                     # Helm chart (renamed)
```

★ = PFactory-original work; everything else inherited from the AIFactory
fork and adapted by string-replace.

## Workspace layout (runtime)

```
~/.pfactory/
├── projects.json                        # { projects: [{ id, name, root_path, created_at }] }
└── workspaces/
    └── {project_id}/
        └── specs/
            └── {spec_id}/
                ├── task.md              # handover payload (markdown)
                ├── status.json          # lifecycle state, lane_progress
                ├── context/             # ← populated by Task 3 snapshotter
                │   ├── source.json      #   { aifactory_spec_dir, branch, base_ref, sha, ... }
                │   ├── aifactory_spec.md    (mode 0o444)
                │   ├── aifactory_plan.json  (mode 0o444)
                │   └── diff.patch       #   base_ref..branch
                ├── tests/               # ← Gen-Functional writes pytest files (Task 6)
                │   └── functional/
                ├── findings/            # ← verdicts.json + triage_report + evidence/
                ├── logs/                # ← per-agent transcripts (Tasks 5+)
                ├── memory/              # ← session insights (Tasks 5+)
                ├── report.md            # ← Triager output (Task 8)
                └── report.json
```

Cross-reference is one-way: PFactory reads `~/.aifactory/workspaces/{project_id}/specs/{spec_id}/`
**read-only** at handover time, copies relevant files into `context/`
at mode 0o444, and operates on the snapshot thereafter. The upstream
AIFactory spec can change without breaking in-flight PFactory work.

## Handover dataflow

```
┌────────────────────┐        /handover-to-pfactory
│ AIFactory project  │  ───►  (Claude Code skill)
│ at <root_path>     │              │
│ branch = feature/* │              │  MCP call over stdio
└────────────────────┘              ▼
                            mcp__pfactory__task_create_and_run
                            { project_id, spec_id, branch, base_ref, confirm }
                                    │
                                    ▼
         ┌──────────────────────────────────────────────┐
         │ apps/backend/agents/tools_pkg/tools/         │
         │   task_control.py                            │
         │ ─────────────────────────────────────────── │
         │ • look up project in projects.json           │
         │ • mkdir ~/.pfactory/workspaces/.../specs/X/  │
         │ • write task.md + status.json (status=pending│
         │ • call snapshot_aifactory_spec(...)          │
         │   if SnapshotError → rollback + MCP error    │
         │ • return { task_id, spec_dir, portal_url }   │
         └────────────────┬─────────────────────────────┘
                          │
                          ▼
            ~/.pfactory/workspaces/.../specs/X/
                          │
                          │  Tasks 5-8 will pick up here:
                          ▼
                  Planner agent reads context/aifactory_spec.md +
                  context/diff.patch and emits test_plan.json
                          │
                          ▼
                  Gen-Functional reads test_plan.json, generates
                  pytest files into tests/functional/ in the workspace
                          │
                          ▼
                  Executor calls dispatch_lane("functional", ...)
                  → DockerRunner.run_pytest(...) in pfactory-runner-python
                          │
                          ▼
                  Evaluator scores coverage delta + flake-lint + mutate-
                  and-check sanity probe → per-test verdicts
                          │
                          ▼
                  Triager dedups + ranks + renders report.md, commits
                  accepted tests to the AIFactory feature branch, runs
                  `gh pr comment <pr>` with the report body
```

## Runner stack (Task 4)

```
                ┌──────────────────────┐
                │   dispatch_lane()    │   thin lane → runner router
                │   lane_dispatch.py   │
                └──────────┬───────────┘
                           │
        ┌──────────────────┼──────────────────────┐
        │                  │                      │
   unit / browser        api / integration         mutation
        │                  │                      │
        ▼                  ▼                      ▼
  DockerRunner       DockerRunner             mutate probe
  (pytest / jest /    + AppRuntime             (mutmut / Stryker
   playwright)        (browser lane)            per language)
  build_argv()  ←── pure function: argv list
                    --network=none
                    --read-only
                    --cpus=2 --memory=2g
                    --pids-limit=512
                    -v repo:/work:ro
                    -v scratch:/scratch:rw
                    --tmpfs /tmp:rw,size=64m
  run()
   └── subprocess.run(...)
   └── collect /scratch/junit.xml + /scratch/coverage.xml
   └── DockerRunResult { returncode, stdout, stderr, argv,
                          junit_xml_path, coverage_xml_path }
```

`DockerRunner` wraps `subprocess` rather than the docker SDK so swapping
docker ↔ podman is a config change. Binary picked from
`PFACTORY_CONTAINER_BIN` env or constructor; default `docker`.

## Tool registry (Task 4)

v0.2 lane spine — security scanning is out of scope (delegated to dedicated
pipelines); see `apps/backend/tools/runners/lang_registry.py` for the source
of truth.

| Language | Unit | Browser | API | Integration | Mutation |
|---|---|---|---|---|---|
| **Python** | pytest ★ | playwright-python | httpx+pytest | testcontainers | mutmut |
| **TypeScript** | jest ★ | playwright ★ | supertest | testcontainers-node | stryker |
| Java / .NET | — | — | — | — | — (v0.3+) |
| Go / Rust / Ruby | — | — | — | — | — (v0.4+) |

★ = lit today (Python unit + TypeScript unit/browser).

★ = the only `available_at_mvp=True` cell.
[`lang_registry.py`](https://github.com/olafkfreund/PFactory/blob/main/apps/backend/tools/runners/lang_registry.py)
holds the live source.

## MCP surface (Task 2)

Seven tools exposed over stdio to Claude Code via
[`.mcp.json`](https://github.com/olafkfreund/PFactory/blob/main/.mcp.json) and
[`scripts/start-pfactory-mcp.sh`](https://github.com/olafkfreund/PFactory/blob/main/scripts/start-pfactory-mcp.sh):

| Tool | Purpose | Side effects |
|---|---|---|
| `task_create_and_run` | Create a PFactory workspace for an AIFactory spec | mkdir workspace + snapshot AIFactory spec |
| `task_status` | Read lifecycle state | read-only |
| `task_list` | List tasks; filter by project / status | read-only |
| `project_list` | List registered AIFactory projects | read-only |
| `project_create` | Register an AIFactory project | append to projects.json |
| `report_get` | Fetch report md / json | read-only |
| `task_rerun` | Re-execute one lane (functional only at MVP) | bump rerun_count + reset lane state |

All seven defined in
[`task_control.py`](https://github.com/olafkfreund/PFactory/blob/main/apps/backend/agents/tools_pkg/tools/task_control.py).

## Module dependency graph

```
        test_plan/  ◄────────┐
        (Lane, Subtask)      │
              ▲              │
              │              │ imports Lane (Task 3+)
              │              │
       workspaces/           │
       snapshotter           │
              ▲              │
              │ used by      │
              │              │
   tools_pkg/tools/          │
     task_control.py  ─────► │
              ▲              │
              │ registered   │
              │              │
       mcp_server/
       pfactory_server.py

       tools/runners/   ◄───── (Task 6 Gen-Functional will import)
       ├── docker_runner.py
       ├── lang_registry.py
       └── lane_dispatch.py
                  ▲
                  │ used by
                  │
       (Task 8 Executor)
```

## Credential Broker (epic #62)

A pluggable secrets layer (`apps/backend/pfactory_secrets/`) so agents can
authenticate to cloud environments without secrets in the repo. It mirrors the
LLM-provider patterns and extends — rather than replaces — the existing
`core/mcp_credentials.py` ambient chain.

```
   agent / MCP tool ─► CredentialBroker.resolve_cloud("gcp"|"aws"|"azure"|"k8s")
                       │  (1) egress gate: .pfactory.yml egress.enabled?  (default OFF)
                       │  (2) backend-fetch head, else ambient mcp_credentials chain
                       ▼
            get_secrets_backend(name)  ◄─ infer_backend_from_ref()  (refs.py)
                       │   env · localfile(sops/age/agenix) · vault ·
                       │   azure_keyvault · aws_secrets_manager · gcp_secret_manager
                       ▼
            materialise ephemerally → env vars + 0600 cred files (kubeconfig,
            ADC json) in a per-task scratch dir, wiped on close()/atexit
                       ▼
            inject into core/client.py agent env (no-op unless egress enabled)
```

| Module | Role |
|---|---|
| `__init__.py` | `SecretsBackend` ABC + `SecretRef` / `SecretValue` (value-redacting `repr`) |
| `refs.py` | per-scheme ref parsing + backend routing (mirrors `infer_provider_from_model`) |
| `factory.py` | `get_secrets_backend()` registry + alias map + lazy SDK import |
| `backends/` | `env`, `localfile`, `vault`, `azure_keyvault`, `aws_secrets_manager`, `gcp_secret_manager` |
| `broker.py` | `CredentialBroker` — cloud resolution, ephemeral materialise + wipe, `inject_task_credentials` |
| `egress.py` | `.pfactory.yml` egress gate + secret-free manifest + badge |
| `redaction.py` | value-based + pattern redaction; `RedactingFilter` for loggers |
| `cli.py` | `python -m pfactory_secrets.cli audit\|doctor\|resolve` |

Design: `docs/plans/2026-05-30-credential-broker-design.md`. Reference:
`guides/credentials.md`. Cloud SDKs are optional (lazy-imported); a missing
package degrades only that backend to `available() == False`.

## What's NOT in the architecture yet

- **Planner / Generator / Evaluator / Triager agents** (Tasks 5-8). Prompts under `apps/backend/prompts/` will be authored as those tasks land.
- **Portal retheme** (Tasks 9-10). The inherited FastAPI app + React frontend are present but still configured for AIFactory's coder pipeline.
- **REST endpoints** for the seven MCP tools. Phase-9 portal will mirror them so the React frontend can read the same state.
- **CI workflow** updates. `.github/workflows/` inherited; pending Task 12's docs+tag pass.
- **factory-core shared lib**. Hard-fork trade-off — accepted infra drift for clean separation. May extract later.

## Development environment (Nix / devenv)

PFactory ships a `flake.nix` declaring a `devShells.default` that gives
you a reproducible NixOS-friendly dev shell. Entry is a single command:

```bash
nix develop
```

Or with [`direnv`](https://direnv.net/) auto-loading:

```bash
direnv allow
```

What's in the shell:

- **Python 3.13** + `uv` for venv management
- **Node.js 22** for the frontend + portal
- **`docker-client`** (the daemon lives on the host) for `DockerRunner` (Task 4)
- **`git`, `gh`, `just`, `ripgrep`, `jq`** — the tools `verify-fork.sh` and friends expect
- Shell functions: **`bootstrap-venv`** (full backend install), **`pfactory-minimal-venv`** (just pytest), **`pfactory-test`** (run the non-SDK suite), **`verify-fork`**

Env defaults (overridable per-shell):

- `PFACTORY_WORKSPACE_ROOT=~/.pfactory`
- `PFACTORY_PORTAL_PORT=3102`
- `PFACTORY_AUTO_PLAN=0` (off by default for deterministic tests; production sets to `1`)

`nix flake check` validates the shell builds across `x86_64-linux`.
`nix fmt` formats `.nix` files via `nixpkgs-fmt`. The legacy `shell.nix`
remains for `nix-shell` users; new development should prefer the flake
path.

## Cross-references

- Full design rationale + 10 locked decisions → [Design Plan]({{ '/design-plan/' | relative_url }})
- Component-level implementation detail → [Technical Spec]({{ '/technical-spec/' | relative_url }})
- TDD plan + test pyramid → [Test Coverage Spec]({{ '/tests/' | relative_url }})
- Per-task breakdown + dependency graph → [Task Breakdown]({{ '/tasks/' | relative_url }})
- Live build status → [Progress]({{ '/progress/' | relative_url }})
