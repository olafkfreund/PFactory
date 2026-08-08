---
layout: default
title: Environment Variable Reference
permalink: /environment-reference/
nav_order: 20
---

# Environment Variable Reference

This is the complete, exhaustive reference for every environment variable the
PFactory service reads. It covers the **backend** (`apps/backend`, the
planner / gen-functional / evaluator / triager agents and their libraries) and
the **web server** (`apps/web-server`, the portal API + PTY + OIDC + crypto).

If you are installing PFactory from scratch, start with
[Required to run](#required-to-run), then the section for whatever feature you
are turning on.

Every entry lists: **Default**, **Required?**, on/off meaning for booleans, a
one-line purpose, and the file it is read in. "How to set" is uniform:

- **docker-compose / local dev**: put the variable in the root `.env` file (see
  `.env.example`). The web server auto-loads `apps/web-server/.env` on import
  (`server/env_bootstrap.py`).
- **Kubernetes / Helm**: set it in the Deployment env or a mounted Secret.
- **Ad hoc**: export it in the shell before launching the process.

## How configuration is loaded

- The web server has a pydantic `BaseSettings` class
  (`apps/web-server/server/config.py`) with **`env_prefix = "APP_"`**. Every
  field `FOO` there is set with `APP_FOO`. Unknown keys are ignored
  (`extra = "ignore"`), so backend `PFACTORY_*` vars can safely live in the same
  `.env`.
- Everything else is read directly with `os.environ` / `os.getenv` throughout
  the backend and web server. Those names are used verbatim (no prefix), except
  the handful noted as `APP_`-prefixed below.
- Booleans are truthy on `1` / `true` / `yes` / `on` (case-insensitive) unless
  noted otherwise.

---

## Required to run

At minimum PFactory needs **one LLM provider credential** (or a Claude
subscription OAuth token) so the agents can call a model. Nothing else is
strictly required — the API token, JWT secret, and SQLite database are all
auto-generated on first run.

| Variable | Default | Required? | Purpose |
| --- | --- | --- | --- |
| One of `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `GOOGLE_API_KEY`/`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, or a configured Ollama endpoint | none | **Yes** (at least one) | Lets the pipeline reach a model. See [Providers & credentials](#providers--credentials). |

Everything below is optional unless its Required? column says otherwise.

---

## Web server (core)

pydantic `Settings`, prefix `APP_`. Read in `apps/web-server/server/config.py`.

| Variable | Default | Required? | Purpose |
| --- | --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | No | Bind address for the portal HTTP server. |
| `APP_PORT` | `3114` | No | Bind port for the portal. |
| `APP_DEBUG` | `false` | No | On: web-server debug mode. (Distinct from backend `DEBUG`.) |
| `APP_SSL_ENABLED` | `false` | No | On: serve HTTPS; self-signs a cert under the data dir if none supplied. |
| `APP_SSL_CERTFILE` | `""` | No | Path to a TLS certificate (used when `APP_SSL_ENABLED`). |
| `APP_SSL_KEYFILE` | `""` | No | Path to the TLS private key. |
| `APP_API_TOKEN` | auto-generated | No | Bearer token for the portal API. Auto-generated to `~/.pfactory/.token` on first run and printed once. Set to pin a known value. |
| `APP_DISABLE_AUTH` | `false` | No | On: inject a default admin and skip **all** auth (incl. the WebSocket terminal). Refused unless `APP_HOST` is loopback (issue #128). Dev only. |
| `APP_ALLOW_INSECURE_AUTH` | `false` | No | Escape hatch that permits `APP_DISABLE_AUTH` on a non-loopback host. **Never set in production.** Read directly via `os.environ` in `config.py`. |
| `APP_JWT_SECRET` | auto-generated | No | HMAC secret for portal JWTs. Persisted to `~/.pfactory/.jwt_secret` so tokens survive restarts. |
| `APP_JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | No | Access-token lifetime. |
| `APP_JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | No | Refresh-token lifetime. |
| `APP_JWT_ALGORITHM` | `HS256` | No | JWT signing algorithm. |
| `APP_DATABASE_URL` | auto (sqlite) | No | Settings alias for the DB URL. See `DATABASE_URL` in [Lifecycle & persistence](#lifecycle-persistence--concurrency) — the DB engine reads the unprefixed name. |
| `APP_MIGRATIONS_AUTO_APPLY` | `true` | No | On: run `alembic upgrade head` at boot. Off: only verify schema is at head (use with an out-of-band Helm migration Job). |
| `APP_PROJECTS_DATA_DIR` | data dir | No | Directory for project metadata + the SQLite DB. Defaults to the platform data dir (`~/.pfactory`). |
| `APP_BACKEND_PATH` | `../backend` | No | Path to `apps/backend` (auto-derived from the web-server location). |
| `APP_CORS_ORIGINS` | localhost set | No | Extra allowed CORS origins. Comma-separated string or JSON list. |
| `APP_DEFAULT_SHELL` | `/bin/bash` | No | Shell for new PTY terminals. |
| `APP_MAX_TERMINALS` | `20` | No | Max concurrent PTY terminals. |
| `APP_MAX_CONCURRENT_TASKS` | `5` | No | Max concurrently executing tasks in the web server. |
| `APP_CFACTORY_SEARCH_URL` | in-cluster CFactory URL | No | CFactory (cockpit) base URL that the portal proxies federated `/api/search` to. Empty disables the proxy. |
| `APP_CFACTORY_READ_KEY` | `""` | No | Read-scoped cockpit key for the federated-search proxy. Empty disables it. |
| `APP_LIVENESS_SWEEP_ENABLED` | `false` | No | On: periodic sweep flags silent in-flight stages as `stalled` (issue #95). |
| `APP_LIVENESS_SWEEP_INTERVAL_SECONDS` | `300` | No | How often the liveness sweep runs. |
| `APP_LIVENESS_SWEEP_DEADLINE_SECONDS` | `600` | No | Idle budget before a stage is marked stalled. |

---

## Auth & access control

| Variable | Default | Required? | On/Off | Purpose | Read in |
| --- | --- | --- | --- | --- | --- |
| `PFACTORY_MCP_SECRET` | `""` | For remote MCP / plan-pipeline | — | Shared secret guarding the MCP JSON-RPC endpoint and the plan-pipeline API. | `routes/mcp_rpc.py`, `routes/plan_pipeline.py`, `plan/access_cli.py` |
| `PFACTORY_MCP_KEY` | `""` | No | — | API key the agent HTTP client sends to the portal. | `agents/tools_pkg/http_client.py` |
| `PFACTORY_MCP_REMOTE_ENABLED` | off | No | On enables the remote MCP surface. | Gate for the remote MCP server. | `mcp_remote/__init__.py` |
| `PFACTORY_MCP_LOOPBACK_URL` | `http://localhost:3114` | No | — | Loopback base URL the remote MCP tools call back into. | `mcp_remote/tools.py` |
| `PFACTORY_API_TOKEN_FILE` | `~/.pfactory/.token` | No | — | File the agent HTTP client reads the API token from. | `agents/tools_pkg/http_client.py` |
| `METRICS_SCRAPE_TOKEN` | `""` | For protected `/metrics` | — | Bearer token required to scrape Prometheus metrics. Empty leaves metrics open. | `observability/metrics.py` |
| `APP_OIDC_ENABLED` | off | For SSO | On turns on OIDC login. | Master switch for OIDC/SSO. | `oidc/client.py` |
| `APP_OIDC_PROVIDER` | `keycloak` | No | — | OIDC preset (keycloak, etc.) selecting default scope/endpoints. | `oidc/presets.py` |
| `APP_OIDC_ISSUER_URL` | none | If OIDC on | — | OIDC issuer/discovery base URL. | `oidc/client.py`, `routes/oidc_routes.py` |
| `APP_OIDC_CLIENT_ID` | none | If OIDC on | — | OIDC client ID. | `oidc/client.py`, `routes/oidc_routes.py` |
| `APP_OIDC_CLIENT_SECRET` | none | If OIDC on | — | OIDC client secret. | `oidc/client.py`, `routes/oidc_routes.py` |
| `APP_OIDC_SCOPE` | preset default | No | — | Override the requested OIDC scope. | `oidc/client.py` |
| `APP_OIDC_REDIRECT_URI` | derived | No | — | Override the callback URL (for reverse proxies / LAN IPs). | `routes/oidc_routes.py` |
| `APP_OIDC_POST_LOGIN_REDIRECT` | `/` | No | — | Where to send the user after login. | `routes/oidc_routes.py` |
| `APP_OIDC_POST_LOGOUT_REDIRECT` | `/` | No | — | Where to send the user after logout. | `routes/oidc_routes.py` |
| `APP_OIDC_USERINFO_CACHE_TTL_S` | `300` | No | — | TTL (seconds) for the OIDC userinfo cache; `0` disables caching. | `oidc/userinfo_cache.py` |
| `APP_OIDC_DEFAULT_ROLE` | `member` | No | — | Role assigned to newly provisioned OIDC users. | `oidc/provisioning.py` |
| `APP_OIDC_GROUP_TO_ROLE` | `""` | No | — | JSON map of OIDC group -> PFactory role. | `oidc/provisioning.py` |
| `APP_OIDC_DEFAULT_ORG_SLUG` | `default` | No | — | Slug of the org new OIDC users join. | `oidc/provisioning.py` |
| `APP_OIDC_DEFAULT_ORG_NAME` | `Default Organization` | No | — | Display name for the default org. | `oidc/provisioning.py` |

---

## Auto-pipeline stage toggles

Each stage auto-advances by default. Set to `0` to stop after that stage and
inspect it. Read in the respective agent modules.

| Variable | Default | Required? | On/Off | Purpose | Read in |
| --- | --- | --- | --- | --- | --- |
| `PFACTORY_AUTO_PLAN` | `1` (on) | No | `0` stops before Planner auto-fire | Planner auto-fires from task create+run. | `agents/planner.py` |
| `PFACTORY_AUTO_GENERATE` | `1` (on) | No | `0` stops before Gen-Functional | Gen-Functional auto-fires from Planner. | `agents/gen_functional.py` |
| `PFACTORY_AUTO_EVALUATE` | `1` (on) | No | `0` stops before Evaluator | Evaluator auto-fires from Gen-Functional. | `agents/evaluator.py` |
| `PFACTORY_AUTO_TRIAGE` | `1` (on) | No | `0` stops before Triager | Triager auto-fires from Evaluator. | `agents/triager.py` |
| `QUICK_MODE` | off | No | `true` shortens prompts | Uses terser prompt variants (faster/cheaper, less thorough). | `prompts_pkg/prompts.py` |

---

## Model selection & routing

| Variable | Default | Required? | Purpose | Read in |
| --- | --- | --- | --- | --- |
| `PFACTORY_ROUTING_POLICY` | none | No | JSON routing policy for the plan decomposer (per-role model routing). | `plan/decompose/routing.py` |
| `PFACTORY_PLANNER_PINNED_MODEL` | none | No | Pin the planner to a single model, overriding routing. | `plan/decompose/routing.py` |
| `PFACTORY_EXECUTION_MODEL` | by complexity | No | Override the execution-profile model (else `simple->haiku`, `standard->sonnet`, `complex->opus`). | `plan/emit/execution_profile.py` |
| `AUTO_BUILD_MODEL` | none | No | Default model for the `auto-build` CLI when `--model` is not passed. | `cli/main.py` |
| `UTILITY_MODEL_ID` | code default | No | Model ID for utility/helper LLM calls. | `core/model_config.py` |
| `UTILITY_THINKING_BUDGET` | `""` | No | Thinking-token budget for utility calls (integer; empty = provider default). | `core/model_config.py` |
| `QA_LLM_PROVIDER` | auto-detected | No | Forces the QA/evaluator provider (e.g. `openai`, `anthropic`). | `phase_config.py` |
| `ANTHROPIC_MODEL` | none | No | SDK model override (from API-profile custom mappings). | `core/auth.py` (SDK passthrough) |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | none | No | Remaps the "haiku" tier to a custom model ID. | `phase_config.py`, `core/auth.py` |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | none | No | Remaps the "sonnet" tier to a custom model ID. | `phase_config.py`, `core/auth.py` |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | none | No | Remaps the "opus" tier to a custom model ID. | `phase_config.py`, `core/auth.py` |

---

## Feasibility, enrichment & recon

| Variable | Default | Required? | Purpose | Read in |
| --- | --- | --- | --- | --- |
| `PFACTORY_BUDGET_MONTHLY_USD` | none | No | Monthly USD budget threshold for the feasibility cost gate. | `plan/feasibility/run.py` |
| `PFACTORY_ENRICH_ADAPTERS` | `""` | No | Comma-separated cloud enrichment adapters to enable (aws, azure, gcp, kubernetes). | `plan/service.py` |
| `PFACTORY_ENRICH_CONNECTORS` | `""` | No | Comma-separated external-knowledge connectors to enable. | `plan/service.py` |
| `PFACTORY_WIKI_ROOT` | none | No | Local wiki root the planner reads for enrichment context. | `plan/service.py` |
| `PFACTORY_RECON_GIT_HOST` | `github.com` | No | Git host used by the recon clone step. | `plan/recon/clone.py` |
| `PFACTORY_RECON_TOKEN` | none | No | Token for recon clone / DORA GitHub reads (falls back to `GH_TOKEN`/`GITHUB_TOKEN`). | `plan/recon/clone.py`, `plan/recon/dora_github.py` |
| `PFACTORY_GITHUB_API_BASE` | `https://api.github.com` | No | GitHub API base for DORA recon (set for GHES). | `plan/recon/dora_github.py` |
| `PFACTORY_CLOUD_ASSESSMENT_ROOT` | data dir | No | Storage root for cloud-assessment artifacts. | `agents/cloud/store.py` |
| `PFACTORY_VISUAL_INSPECTION_ROOT` | data dir | No | Storage root for visual-inspection artifacts. | `agents/visual_inspection/store.py` |

---

## Docs emission targets

| Variable | Default | Required? | On/Off | Purpose | Read in |
| --- | --- | --- | --- | --- | --- |
| `PFACTORY_DOCS_DIR` | repo default | No | — | Override the output directory for emitted docs. | `plan/emit/docs/emit_docs.py` |
| `PFACTORY_DOCS_BACKSTAGE` | off | No | On emits to Backstage | Enable Backstage doc emission (also implied by `BACKSTAGE_BASE_URL`). | `plan/emit/docs/emit_docs.py` |
| `PFACTORY_DOCS_CONFLUENCE` | off | No | On emits to Confluence | Enable Confluence doc emission (also implied by `CONFLUENCE_BASE_URL`). | `plan/emit/docs/emit_docs.py` |
| `BACKSTAGE_BASE_URL` | `""` | For Backstage | — | Backstage instance base URL. | `plan/emit/docs/targets/backstage.py` |
| `CONFLUENCE_BASE_URL` | `""` | For Confluence | — | Confluence base URL. | `plan/emit/docs/targets/confluence.py` |
| `CONFLUENCE_API_TOKEN` | `""` | For Confluence | — | Confluence API token. | `plan/emit/docs/targets/confluence.py` |
| `CONFLUENCE_SPACE` | `""` | For Confluence | — | Target Confluence space key. | `plan/emit/docs/targets/confluence.py` |

---

## Lifecycle, persistence & concurrency

| Variable | Default | Required? | Purpose | Read in |
| --- | --- | --- | --- | --- |
| `DATABASE_URL` | sqlite under data dir | No | Primary DB URL (Postgres in prod, e.g. `postgresql+asyncpg://...`). Read unprefixed by the DB engine, jobstore, alembic, and crypto tooling; `APP_DATABASE_URL` is the Settings alias. | `web-server/server/database/engine.py`, `jobstore/store.py`, `database/alembic/env.py`, `plan/service.py` |
| `PFACTORY_WORKSPACE_ROOT` | `~/.pfactory` | No | Workspace root the portal + agents read/write. | `agents/tools_pkg/tools/task_control.py`, `agents/liveness_sweep.py`, `routes/pfactory_tasks.py` |
| `PFACTORY_WORKSPACES_DIR` | derived | No | Override the per-plan workspaces directory. | `plan/service_helpers.py` |
| `PROJECT_WORKSPACE_ROOT` | derived | No | Root for project workspaces in the web server. | `services/project_workspace_service.py` |
| `PFACTORY_PLAN_PERSIST` | off | No | On: persist plans to disk. | `plan/service_helpers.py` |
| `PFACTORY_PLAN_STORE_DIR` | derived | No | Directory for persisted plans. | `plan/service_helpers.py` |
| `PFACTORY_MAX_CONCURRENT_PLANS` | `4` | No | Cap on concurrent plan runs. | `plan/service.py`, `web-server/server/jobstore/store.py` |
| `PFACTORY_PLAN_LEASE_TTL_SECONDS` | `600` | No | Lease TTL (seconds) for a plan job in the durable jobstore before it can be reclaimed. | `web-server/server/jobstore/store.py` |
| `PFACTORY_MULTI_TENANT` | off | No | On (hosted mode): scope plan sessions by the `X-Tenant-Id` intake header. Off: single-tenant, behaviour unchanged. | `web-server/server/tenancy.py`, `plan/service.py` |
| `PFACTORY_STALL_DEADLINE_SECONDS` | `900` | No | Idle seconds before an agent run is considered stalled. | `agents/liveness.py` |
| `PFACTORY_PORTAL_PORT` | `3114` | No | Portal port the task-control tool links back to. | `agents/tools_pkg/tools/task_control.py` |
| `PFACTORY_API_URL` | `http://localhost:3114` | No | Base URL agents/CLI use to reach the portal API. | `agents/tools_pkg/http_client.py`, `plan/access_cli.py` |
| `PFACTORY_SPEC_DIR` | none | No | Spec directory the MCP server serves. | `mcp_server/pfactory_server.py` |
| `PFACTORY_PROJECT_DIR` | `CLAUDE_PROJECT_DIR` | No | Project directory for the MCP server (falls back to `CLAUDE_PROJECT_DIR`). | `mcp_server/pfactory_server.py` |
| `PFACTORY_SKILLS_DIR` | bundled | No | Directory of agent skills the portal serves. | `routes/pfactory_skills.py` |
| `APP_SKILLS_PATH` | bundled | No | Filesystem path for the skills service. | `services/skills_service.py` |
| `PFACTORY_DOCS_DIR` | repo default | No | (See Docs emission.) | `plan/emit/docs/emit_docs.py` |
| `PFACTORY_BATCH_MIN_JOBS` | `2` | No | Min jobs before insight-extraction batches. | `analysis/insight_extractor.py` |
| `PFACTORY_BATCH_TIMEOUT` | `120` | No | Seconds to wait to fill an insight-extraction batch. | `analysis/insight_extractor.py` |
| `PFACTORY_BATCH_DISABLE` | off | No | On: disable insight-extraction batching. | `analysis/insight_extractor.py` |

---

## Handoff, completion & stage events

| Variable | Default | Required? | On/Off | Purpose | Read in |
| --- | --- | --- | --- | --- | --- |
| `PFACTORY_AIFACTORY_ROOT` | none | For handoff/snapshot | — | Local path to the AIFactory checkout PFactory operates on. | `workspaces/snapshotter.py` |
| `PFACTORY_AIFACTORY_API_URL` | `http://localhost:3101` | No | — | AIFactory API base URL. | `workspaces/snapshotter.py`, `plan/service.py` |
| `PFACTORY_AIFACTORY_API_TOKEN` | none | For handoff | — | Auth token for the AIFactory API (plan-pipeline handoff). | `routes/plan_pipeline.py` |
| `PFACTORY_AIFACTORY_HANDSHAKE` | off | No | On enables the handshake | Emit the AIFactory contract handshake. | `plan/emit/contract_emit.py` |
| `PFACTORY_HANDBACK_PREPARE` | off | No | On prepares handback | Prepare a TFactory->AIFactory correction handback. | `agents/handback/trigger.py` |
| `PFACTORY_HANDBACK_SEND` | off | No | On sends handback | Actually send the handback to AIFactory. | `agents/handback/trigger.py` |
| `PFACTORY_HANDBACK_MAX_CYCLES` | `2` | No | — | Cap on correction-cycle iterations. | `agents/handback/loop.py` |
| `PFACTORY_COMPLETION_SENTINEL` | off | No | On writes a sentinel | Write a completion sentinel file on finish. | `plan/completion.py`, `agents/triager.py` |
| `PFACTORY_COMPLETION_SENTINEL_DIR` | derived | No | — | Directory for the completion sentinel. | `plan/completion.py` |
| `PFACTORY_COMPLETION_WEBHOOK` | none | No | — | URL POSTed on pipeline completion. | `plan/completion.py`, `agents/triager.py` |
| `PFACTORY_COMPLETION_WEBHOOK_TIMEOUT` | `5` | No | — | Timeout (seconds) for the completion webhook. | `plan/completion.py`, `agents/triager.py` |
| `PFACTORY_STAGE_EVENT_SENTINEL` | off | No | On writes per-stage sentinels | Emit a sentinel on each stage transition. | `agents/stage_events.py` |
| `PFACTORY_STAGE_EVENT_WEBHOOK` | none | No | — | URL POSTed on each stage transition. | `agents/stage_events.py` |
| `PFACTORY_STAGE_EVENT_WEBHOOK_TIMEOUT` | `5` | No | — | Timeout (seconds) for the stage-event webhook. | `agents/stage_events.py` |
| `PFACTORY_COPILOT_DISPATCH_ENABLED` | off | No | On enables Copilot dispatch | Enable GitHub Copilot dispatch from the web server. | `services/copilot_dispatch_service.py` |
| `PFACTORY_PLAN_REVIEW_COMMENT` | off | No | On posts a review comment | Post the plan review as a GitHub comment. | `routes/github.py` |

---

## Security & execution

| Variable | Default | Required? | On/Off | Purpose | Read in |
| --- | --- | --- | --- | --- | --- |
| `PFACTORY_TRIAGER_GIT_WRITE` | `0` (dry-run) | No | On commits accepted tests | Let the triager commit accepted tests to the feature branch. Keep off unless confirmed safe. | `agents/triager.py` |
| `PFACTORY_TRIAGER_PR_COMMENT` | `0` (dry-run) | No | On posts a PR comment | Let the triager post the triage report via `gh pr comment`. | `agents/triager.py` |
| `PFACTORY_TRIAGER_HARVEST` | code default | No | — | Toggle harvesting accepted tests. | `agents/triager.py` |
| `PFACTORY_TRIAGER_HARVEST_GLOBAL` | off | No | On harvests globally | Global test-harvest mode. | `agents/triager.py` |
| `PFACTORY_EGRESS_ENABLED` | off | No | On allows network egress | Allow outbound network from the secrets/egress guard. | `pfactory_secrets/egress.py` |
| `PFACTORY_ALLOW_API_KEYS` | off | No | On permits raw API keys | Allow provider API keys (vs OAuth-only) in the auth policy. | `providers/auth_policy.py` |
| `PFACTORY_STRICT_COMMAND_PARSING` | off | No | On enforces strict parsing | Strict command parsing in the security parser. | `security/parser.py` |
| `PFACTORY_RED_TEAM_RISK_THRESHOLD` | `high` | No | — | Severity at/above which red-team findings block (`low`/`medium`/`high`/`critical`). | `plan/review/lenses/red_team.py` |
| `PFACTORY_CONTAINER_BIN` | `docker` | No | — | Container CLI for the sandboxed test runner (`docker` or `podman`). | `tools/runners/docker_runner.py` |
| `PFACTORY_DOCKER_IMAGE_PYTHON` | `pfactory-runner-pytest:latest` | No | — | Image for the Executor's sandboxed pytest run. (Consumed by the runner config.) | runner config / `.env` |
| `PFACTORY_RUNNER_REGISTRY` | `ghcr.io/olafkfreund` | No | Empty string restores bare local tags | Registry/namespace that bare `pfactory-runner-*` tags resolve to, so a lane runs the image CI published from a commit rather than whatever was last built on the machine (#449). Any other value points at a mirror or a fork's GHCR. Images that are already qualified (contain a `/`) and non-runner images are never rewritten. | `tools/runners/docker_runner.py` |
| `PFACTORY_TEST_AGENT_CMD` | `""` | No | — | E2E test hook: replaces the real agent command with a stub (test harness only). | `services/agent_service.py` |
| `AIFACTORY_BASH_SANDBOX` | `true` | No | Off disables the bash sandbox | Toggle the bash sandbox for agent tool calls. | `core/client.py` |
| `PFACTORY_EXTENSION_REGISTRY` | bundled | No | — | Path override for the review-extension registry. | `plan/review/extension_registry.py` |
| `PFACTORY_RMUX_ENABLED` | off | No | On enables rmux | Enable the rmux terminal multiplexer integration. | `rmux/integration.py` |

---

## Secrets backends & KMS

The web-server crypto layer selects a KMS backend and encrypts stored secrets.

| Variable | Default | Required? | Purpose | Read in |
| --- | --- | --- | --- | --- |
| `APP_KMS_BACKEND` (alias `KMS_BACKEND`) | fernet | No | Selects the KMS backend (`fernet`, `vault`, `aws`, `azure`, `gcp`). | `crypto/kms/__init__.py`, `crypto/__main__.py` |
| `APP_KMS_FERNET_KEY` (alias `KMS_FERNET_KEY`) | none | For fernet backend | Fernet symmetric key used to encrypt stored secrets. | `crypto/kms/fernet.py` |
| `VAULT_ADDR` | `""` | For vault | HashiCorp Vault address. | `crypto/kms/vault.py`, `pfactory_secrets/backends/vault.py` |
| `VAULT_TOKEN` | `""` | For vault | Vault auth token. | `crypto/kms/vault.py`, `pfactory_secrets/backends/vault.py` |
| `VAULT_TRANSIT_KEY` | default key | No | Vault transit key name. | `crypto/kms/vault.py` |
| `VAULT_TRANSIT_MOUNT` | default mount | No | Vault transit mount point. | `crypto/kms/vault.py` |
| `VAULT_NAMESPACE` | none | No | Vault namespace (Enterprise). | `crypto/kms/vault.py` |
| `AWS_KMS_KEY_ID` | none | For aws KMS | AWS KMS key ID for the aws backend. | `crypto/kms/aws.py` |
| `AWS_ENDPOINT_URL` | none | No | Custom AWS endpoint (e.g. LocalStack). | `crypto/kms/aws.py` |
| `AZURE_KEYVAULT_URL` | none | For azure KMS | Azure Key Vault URL. | `crypto/kms/azure.py` |
| `AZURE_KEYVAULT_KEY` | none | For azure KMS | Azure Key Vault key name. | `crypto/kms/azure.py` |
| `GCP_KMS_KEY_NAME` | none | For gcp KMS | GCP KMS key resource name. | `crypto/kms/gcp.py` |

The `pfactory_secrets` local-file backend also reads whatever env var a secret
`env:` locator names (`pfactory_secrets/backends/localfile.py`,
`pfactory_yml/secrets.py`, `core/mcp_credentials.py`). Those names are supplied
by your `pfactory.yml`, not fixed by PFactory — see
[Dynamic / mechanism-based reads](#dynamic--mechanism-based-reads).

---

## Memory (Graphiti) & insight extraction

Read in `apps/backend/integrations/graphiti/config.py` (and
`query_memory.py`, `analysis/insight_extractor.py`).

| Variable | Default | Required? | Purpose |
| --- | --- | --- | --- |
| `GRAPHITI_ENABLED` | off | No | Master switch for the Graphiti memory subsystem. |
| `GRAPHITI_MCP_URL` | none | No | If set, use a remote Graphiti MCP server. |
| `GRAPHITI_LLM_PROVIDER` | `openai` | No | LLM provider for memory operations. |
| `GRAPHITI_EMBEDDER_PROVIDER` | `openai` | No | Embedding provider for memory. |
| `GRAPHITI_DATABASE` | `magestic_ai_memory` | No | Memory database name. |
| `GRAPHITI_DB_PATH` | `~/.pfactory/memories` | No | On-disk path for the memory DB. |
| `GRAPHITI_ANTHROPIC_MODEL` | `claude-sonnet-4-5` | No | Anthropic model when the memory provider is anthropic. |
| `INSIGHT_EXTRACTION_ENABLED` | `true` | No | Toggle post-run insight extraction. |
| `INSIGHT_EXTRACTOR_MODEL` | `claude-3-5-haiku-latest` | No | Model used for insight extraction. |
| `BMAD_SESSION_SEGMENTATION` | off | No | Toggle BMAD session segmentation. |

Provider keys/models for memory (`OPENAI_*`, `AZURE_OPENAI_*`, `VOYAGE_*`,
`GOOGLE_*`, `OPENROUTER_*`, `OLLAMA_*`, `ANTHROPIC_API_KEY`) are shared with the
main pipeline — see below.

---

## Providers & credentials

The provider factory and the Graphiti config both read these. Set the keys for
whichever providers you use.

### LLM providers

| Variable | Default | Required? | Purpose | Read in |
| --- | --- | --- | --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | none | One provider required | Claude subscription OAuth token (preferred over an API key). Also set via the portal OAuth flow. | `core/auth.py`, `core/client.py`, `services/agent_failover.py`, others |
| `ANTHROPIC_API_KEY` | none | One provider required | Anthropic API key. | `integrations/graphiti/config.py`, `runners/changelog_runner.py` |
| `ANTHROPIC_AUTH_TOKEN` | none | No | CCR/proxy token for enterprise Anthropic setups. | `core/auth.py` |
| `ANTHROPIC_BASE_URL` | provider default | No | Custom Anthropic API base URL. | `cli/utils.py`, `core/auth.py` |
| `OPENAI_API_KEY` | none | One provider required | OpenAI API key. | `phase_config.py`, `integrations/graphiti/config.py`, `routes/cli_accounts.py` |
| `OPENAI_MODEL` | `gpt-5-mini` | No | OpenAI model (memory). | `integrations/graphiti/config.py` |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | No | OpenAI embedding model. | `integrations/graphiti/config.py` |
| `OPENAI_COMPATIBLE_BASE_URL` | `https://api.openai.com` | No | Base URL for an OpenAI-compatible endpoint. | `phase_config.py` |
| `OPENAI_COMPATIBLE_API_KEY` | none | No | Key for an OpenAI-compatible endpoint. | `phase_config.py` |
| `AZURE_OPENAI_API_KEY` | none | One provider required | Azure OpenAI key. | `integrations/graphiti/config.py` |
| `AZURE_OPENAI_BASE_URL` | none | For Azure OpenAI | Azure OpenAI endpoint. | `integrations/graphiti/config.py` |
| `AZURE_OPENAI_LLM_DEPLOYMENT` | none | For Azure OpenAI | Azure OpenAI LLM deployment name. | `integrations/graphiti/config.py` |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | none | For Azure OpenAI | Azure OpenAI embedding deployment name. | `integrations/graphiti/config.py` |
| `GOOGLE_API_KEY` | none | One provider required | Google Gemini API key (also read as `GEMINI_API_KEY`). | `phase_config.py`, `integrations/graphiti/config.py`, `routes/cli_accounts.py` |
| `GEMINI_API_KEY` | none | No | Alias for the Gemini key. | `phase_config.py`, `routes/cli_accounts.py` |
| `GOOGLE_LLM_MODEL` | `gemini-2.0-flash` | No | Google LLM model (memory). | `integrations/graphiti/config.py` |
| `GOOGLE_EMBEDDING_MODEL` | `text-embedding-004` | No | Google embedding model. | `integrations/graphiti/config.py` |
| `VOYAGE_API_KEY` | none | No | Voyage embeddings key. | `integrations/graphiti/config.py` |
| `VOYAGE_EMBEDDING_MODEL` | `voyage-3` | No | Voyage embedding model. | `integrations/graphiti/config.py` |
| `OPENROUTER_API_KEY` | none | No | OpenRouter key. | `integrations/graphiti/config.py` |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | No | OpenRouter base URL. | `integrations/graphiti/config.py` |
| `OPENROUTER_LLM_MODEL` | `anthropic/claude-3.5-sonnet` | No | OpenRouter LLM model. | `integrations/graphiti/config.py` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | No | Local Ollama base URL (memory). | `integrations/graphiti/config.py` |
| `OLLAMA_CLOUD_BASE_URL` | `https://ollama.com` | No | Ollama Cloud base URL (provider factory). | `providers/factory.py` |
| `OLLAMA_API_KEY` | none | No | Ollama Cloud API key. | `providers/factory.py` |
| `OLLAMA_LLM_MODEL` | `""` | No | Ollama LLM model. | `integrations/graphiti/config.py` |
| `OLLAMA_EMBEDDING_MODEL` | `""` | No | Ollama embedding model. | `integrations/graphiti/config.py` |
| `OLLAMA_EMBEDDING_DIM` | `0` | No | Ollama embedding dimension. | `integrations/graphiti/config.py` |

### Cloud provider credentials (enrichment + KMS)

Standard cloud SDK credential vars, read by the enrichment adapters
(`plan/enrich/adapters/*`), provider probes (`plan/providers/*`), and the KMS
backends. Set them the usual way for your cloud.

| Variable | Purpose |
| --- | --- |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE`, `AWS_REGION`, `AWS_DEFAULT_REGION` | AWS auth/region for enrichment + KMS. |
| `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CONFIG_DIR` | Azure auth/context for enrichment + KMS. |
| `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, `GCLOUD_PROJECT`, `CLOUDSDK_CONFIG` | GCP auth/project for enrichment + KMS. |
| `KUBECONFIG` | Kubeconfig for the Kubernetes enrichment adapter + deploy lanes. |

### Git / GitHub credentials

| Variable | Default | Purpose | Read in |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | none | GitHub token for provider/recon/clone operations. | `providers/factory.py`, `plan/recon/*` |
| `GH_TOKEN` | none | Alias for the GitHub token. | `providers/factory.py`, `plan/recon/*` |
| `DEFAULT_BRANCH` | repo default | Default branch used by worktree/workspace commands. | `core/worktree.py`, `cli/workspace_commands.py` |
| `FACTORY_SERVICE_NAME` | `the Factory` | Service name interpolated into GitLab MR text. | `runners/github/providers/gitlab_provider.py` |

### Email OAuth (portal integrations)

| Variable | Required? | Purpose | Read in |
| --- | --- | --- | --- |
| `APP_EMAIL_GOOGLE_CLIENT_ID` / `APP_EMAIL_GOOGLE_CLIENT_SECRET` | For Gmail integration | Google OAuth app credentials for the email integration. | `_get_email_oauth_credentials.py` |
| `APP_EMAIL_MICROSOFT_CLIENT_ID` / `APP_EMAIL_MICROSOFT_CLIENT_SECRET` | For Outlook integration | Microsoft OAuth app credentials for the email integration. | `_get_email_oauth_credentials.py` |
| `EMAIL_OAUTH_REDIRECT_URI` | No | Override the email OAuth callback URL (reverse proxy / LAN IP). | `routes/email.py` |

---

## Claude Agent SDK passthrough

When set, these are forwarded to the underlying Claude Agent SDK subprocess
(collected in `SDK_ENV_VARS`, `core/auth.py`). PFactory does not interpret them;
they tune the SDK. All optional.

`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`,
`ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`,
`ANTHROPIC_DEFAULT_OPUS_MODEL`, `NO_PROXY`, `DISABLE_TELEMETRY`,
`DISABLE_COST_WARNINGS`, `API_TIMEOUT_MS`.

---

## CLI / terminal & standard OS vars honored

These are read for CLI cosmetics or standard shell behaviour. Rarely set for the
service itself, but honored where present.

| Variable | Default | Purpose | Read in |
| --- | --- | --- | --- |
| `DEBUG` | off | Backend debug logging (truthy: `1`/`true`/`yes`/`on`). Distinct from `APP_DEBUG`. | `core/debug.py`, `core/client.py`, `ui/status.py`, `core/phase_event.py` |
| `DEBUG_LEVEL` | `1` | Backend debug verbosity level. | `core/debug.py` |
| `DEBUG_LOG_FILE` | none | File to write backend debug logs to. | `core/debug.py` |
| `USE_CLAUDE_MD` | off | On (`true`): load `CLAUDE.md` project context. | `core/client.py` |
| `ENABLE_FANCY_UI` | `true` | Toggle rich CLI UI. | `ui/capabilities.py` |
| `NO_COLOR` | unset | Standard: disable colored output. | `ui/capabilities.py`, `cli/mcp_commands.py` |
| `FORCE_COLOR` | unset | Standard: force colored output. | `ui/capabilities.py` |
| `TERM` | unset | Terminal type for capability detection. | `ui/capabilities.py` |
| `SHELL` | `/bin/bash` | Default shell for PTY sessions. | `web-server/server/pty/session.py` |
| `EDITOR` | unset | Editor to open for interactive review. | `review/reviewer.py` |
| `CI` | unset | `true` selects non-interactive review (CI runners set this automatically). | `review/reviewer.py` |
| `XDG_RUNTIME_DIR` | unset | Runtime dir for the rmux wrapper (standard XDG). | `web-server/server/rmux/wrapper.py` |

---

## Frontend (build-time)

`apps/frontend-web` is a Vite SPA. These are **build-time** (`import.meta.env`),
baked into the bundle at `npm run build`, not read by the running service. Set
them in the frontend build environment if you deploy portals at non-default
URLs.

`VITE_API_BASE_URL`, `VITE_API_URL`, `VITE_WS_BASE_URL`, `VITE_PFACTORY_URL`,
`VITE_AIFACTORY_URL`, `VITE_TFACTORY_URL`, `VITE_CFACTORY_URL`, `VITE_DEBUG`
(plus Vite built-ins `DEV`, `NODE_ENV`).

---

## Dynamic / mechanism-based reads

These read env vars whose **names are not fixed by PFactory** — they come from
your configuration:

- **`pfactory.yml` `env:` secret locators** — a secret reference like
  `env:MY_TOKEN` makes PFactory read `MY_TOKEN`. Resolved in
  `pfactory_secrets/backends/localfile.py`, `pfactory_secrets/probe.py`,
  `pfactory_yml/secrets.py`, `core/mcp_credentials.py`, `byo_llm.py`. Name what
  you like; export the matching variable.
- **Review-extension flags** — each review extension `some-name` has an implicit
  override `PFACTORY_SOME_NAME` (uppercased, hyphens->underscores). Set it truthy
  to force-enable that extension. `plan/review/extension_registry.py`.

---

## Intentionally excluded (incidental)

The following matched an `os.environ` grep but are **not** PFactory
configuration — they are documentation strings, example code, or names read only
inside analyzers that scan *other* projects' source:

- `API_KEY`, `VAR`, `VAR_NAME` — appear only in example/help strings inside
  `security/git_validators.py` and `analysis/analyzers/context/env_detector.py`.
- `PORT` — appears only in a comment example inside
  `analysis/analyzers/port_detector.py` (that analyzer detects ports in scanned
  repos; PFactory's own port is `APP_PORT`).
- `PYTEST_CURRENT_TEST` — set by pytest; only used to detect a test run in the
  auth-host guard (`config.py`), never operator-set.

### Helper scripts (out of service scope)

These are read only by developer/demo helpers under `scripts/`, not by the
running service (backend or web server). Listed for audit completeness:

- `PFACTORY_INSPECT_PORT` — port for the local debug inspect server
  (`scripts/inspect_server.py`, default `3188`).
- `PFACTORY_API` — base URL used by the `sync-claude-auth.py` dev helper
  (`scripts/sync-claude-auth.py`, default `http://127.0.0.1:3198`). Distinct
  from the service-facing `PFACTORY_API_URL`.
- `TF_MODEL` — model id the `scripts/demo/*.py` end-to-end demo drivers call.

No other names were left unaccounted for.

---

## Completeness ledger

- **Backend + web-server names found by grep** (`os.environ` / `os.getenv`,
  constant/list-resolved names, and the 27 pydantic `APP_`-prefixed Settings
  fields; tests excluded): **223**.
- **Documented above as real configuration**: **219**.
- **Intentionally excluded as incidental**: **4** (`API_KEY`, `VAR`, `VAR_NAME`,
  `PORT`) — all appear only in example/help/comment strings. The runtime-only
  `PYTEST_CURRENT_TEST` (membership check in the auth-host guard) is noted for
  completeness but is not operator config.
- **Unaccounted for**: **0**. Every found name is either documented above or in
  the excluded list.
- Frontend build-time `VITE_*` names are listed separately (not part of the
  service runtime count).
- Developer/demo helpers under `scripts/` read three further names
  (`PFACTORY_INSPECT_PORT`, `PFACTORY_API`, `TF_MODEL`); these are outside the
  service scope and are listed under [Helper scripts](#helper-scripts-out-of-service-scope).
