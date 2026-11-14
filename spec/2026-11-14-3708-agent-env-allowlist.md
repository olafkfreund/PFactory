---
status: draft
issue: olafkfreund/Factory#3708
intent: intent/2026-11-14-3708-agent-env-allowlist.md
---

# Spec: agent children get an allowlisted environment

The same spec is committed in PFactory and TFactory; the site tables name the
files of both. This copy governs PFactory.

## Design

### One helper: `apps/backend/core/child_env.py` (new)

A port of AIFactory's rules (`core/auth.py` and `core/child_env.py`, #1680,
#1757), with the same function names so a later shared package is a move, not
a rewrite. It holds:

- `is_denied_env_key(name)`: exact names plus the pattern
  `SECRET|PASSWORD|PRIVATE_KEY|CREDENTIAL|(^|_)KMS|PASSPHRASE`. Exact names:
  `API_TOKEN`, `APP_API_TOKEN`, `JWT_SECRET`, `APP_JWT_SECRET`,
  `DATABASE_URL`, `APP_DATABASE_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`,
  `GITHUB_TOKEN`, `GH_TOKEN`, the provider API keys, the cloud and Vault
  credentials (AIFactory's list), and this service's own tokens
  (`PFACTORY_MCP_KEY`, `PFACTORY_API_TOKEN_FILE` and the other `PFACTORY_*TOKEN*` /
  `*KEY*` names the backend reads).
- `is_agent_env_allowed(name)`: true for the keep set (Claude auth and SDK
  passthrough), for AIFactory's allow pattern (`PATH`, locale, `TERM`,
  `TMPDIR`, CA bundles, proxies, `CLAUDE_CODE_*`, `NIX_*`, Playwright
  paths, git author names), and for names the operator lists in
  `PFACTORY_AGENT_ENV_EXTRA` (comma-separated). A denied name is never allowed,
  not even through the opt-in.
- `get_agent_env_blanks()`: `{name: ""}` for every host variable that is not
  allowed. The Claude SDK builds the CLI's env as
  `{**os.environ, **options.env}` (checked in the installed
  `claude_agent_sdk/_internal/transport/subprocess_cli.py`), so a variable can
  only be neutralised by overriding it with an empty value.
- `child_env(keep=(), extra=None)`: a new dict with only the allowed host
  variables, plus the `keep` names that are set, plus `extra`. Used for
  every child we spawn ourselves.
- Names dropped and not secret-shaped are logged once per process (names
  only), so an operator can see what to add to the opt-in.

One deliberate difference from AIFactory: `HOME`, `XDG_*_HOME` and
`CODEX_HOME` are allowed. AIFactory gives each CLI a private home
(`AgentHome`); these services do not have that yet, and the CLIs find their
login under `HOME`. They are paths, not credentials. AIFactory's git pins are
not ported: no agent child here runs git with our credentials.

### Spawn sites (grep of `origin/dev`, both repos)

Agent children, changed to the allowlist:

| Site | Child | Credentials kept, and why |
| ---- | ----- | ------------------------- |
| `core/client.py` `create_client` | Claude CLI via SDK | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` and the `SDK_ENV_VARS` model settings: the CLI cannot reach the model without them. Task-scoped broker credentials (`inject_task_credentials`, off by default) still win, because blanks go under the explicit values. |
| `core/simple_client.py` | Claude CLI via SDK | Same Claude auth. |
| `providers/codex_agentic.py`; TF `providers/codex.py` | `codex` | `OPENAI_API_KEY`, `CODEX_API_KEY`: codex's own auth. PFactory's `scrub_api_keys` policy still removes them unless `PFACTORY_ALLOW_API_KEYS=1`. |
| `providers/gemini_agentic.py`; TF `providers/gemini.py` | `gemini` | `GEMINI_API_KEY`, `GOOGLE_API_KEY`: gemini's own auth (same policy in PFactory). |
| `providers/copilot_agentic.py` | `copilot` | `COPILOT_GITHUB_TOKEN` only. This is the narrow GitHub token: the pod's `GITHUB_TOKEN` / `GH_TOKEN` (repo write) is not passed. |
| `tools/executor.py` bash and grep | the shell a non-Claude provider's agent drives | None. |
| web server `services/clarification_service.py`, `services/insights_service.py`, `services/insights_providers/claude_provider.py` | `claude --print` | The OAuth token the service resolves and sets itself. |
| web server `services/insights_providers/codex_provider.py`, `gemini_provider.py` | `codex`, `gemini` | Same as the backend runners. |

Children that run code from the repository under test, changed the same way:

| Site | Child | Credentials kept |
| ---- | ----- | ---------------- |
| `agents/preflight_static.py` introspection | `python -c` that imports a project module | None. `PYTHONPATH` is set by the caller. |
| TF `agents/evaluator.py` local pytest (`sh -c`) | the coder's tests | None. `PYTHONPATH` and `extra_env` are set by the caller. |

Not changed, on purpose:

- The first-party runner (`run.py`, and the changelog and PR-review runners)
  started by `services/agent_service.py`, `agent_failover.py`,
  `changelog_service.py` and `pr_review_service.py`. It is our code, it
  builds the clients above, and it reads the database, S3, `APP_API_TOKEN`
  (handback, completion) and `GITHUB_TOKEN`. `make_subprocess_env` keeps its
  current behaviour.
- git and `gh` calls with a fixed argv, the docker and Kubernetes sandbox
  runners (they already pass an explicit env into the container), and the
  interactive PTY shell.

## Alternatives rejected

- A denylist only (drop the known secret names): a new secret added to the pod
  would leak until someone remembers the list. AIFactory moved from this to an
  allowlist in #1757 for that reason.
- Scrubbing `os.environ` of the runner process itself: the runner needs those
  values after the agent starts (handback, artifacts).
- One shared `factory-sandbox` package now: out of scope by the issue; the
  follow-up issue tracks it.
- Giving copilot the pod `GITHUB_TOKEN`: it carries repo write for the control
  plane. A deployment that uses copilot sets `COPILOT_GITHUB_TOKEN`.

## Risks

- An agent step that silently relied on an inherited variable (for example
  `DOCKER_HOST`, `KUBECONFIG`, a cloud profile, an MCP server's token) loses
  it. Mitigation: dropped names are logged once, and `PFACTORY_AGENT_ENV_EXTRA`
  restores non-secret names without a release. Broker credentials are
  unaffected.
- A copilot deployment that authenticated through the pod `GITHUB_TOKEN`
  stops authenticating until `COPILOT_GITHUB_TOKEN` is set.
- Residual, not closed here: the agent runs under the same UID as the runner
  and the web server, so `/proc/<pid>/environ` of those processes and the
  CLI login files under `HOME` stay readable where the bash sandbox does not
  hide them. This is the OS-isolation remainder (AIFactory#363 class) and goes
  in the follow-up issue.
- Stdio MCP servers started by the Claude CLI inherit the blanked env; a
  server that needs a token must get it from its own config `env`.

## Verification

- Unit tests for the predicates and `child_env`: with `APP_API_TOKEN`,
  `APP_JWT_SECRET`, `DATABASE_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`,
  `GITHUB_TOKEN` and `GH_TOKEN` set, none is in the result, the opt-in
  cannot bring one back, and the Claude auth names survive.
- Per-site tests: each spawn site is called with those variables set and the
  env it passes (the `env=` of the subprocess call, or `options.env` merged
  over the host env the way the SDK does it) has none of them non-empty.
- One real-process test: the executor's bash runs `env` and the output has
  none of the secret values.
- The repo's existing tests for the touched files, and the ruff/mypy ratchet.
