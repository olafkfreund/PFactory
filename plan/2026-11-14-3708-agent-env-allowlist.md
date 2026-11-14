---
status: approved
issue: olafkfreund/Factory#3708
spec: spec/2026-11-14-3708-agent-env-allowlist.md
coders:
  1: codex    # small security-critical logic (the allow and deny rules); written once in PFactory, copied to TFactory by the lead
  2: coder    # SDK and MCP wiring, a few lines per file
  3: coder    # provider and executor call sites
  4: coder    # web-server call sites
  5: coder    # repository-execution call sites
  6: coder    # per-site tests
reviewers:
  - codex     # security review of the diff (owner request); agy is out of use (#2748)
---

# Plan: agent children get an allowlisted environment (PFactory)

Agents and children that run repository code inherit the control plane's
environment today, including `APP_API_TOKEN`, `APP_JWT_SECRET`,
`DATABASE_URL`, `S3_ACCESS_KEY` / `S3_SECRET_KEY` and `GITHUB_TOKEN`. This
plan adds one helper with AIFactory's rules and uses it at every such spawn
site. Paths are relative to `apps/`. Line numbers are for `origin/dev` at
branch time; find the code by the quoted text if they moved.

## Approved decisions

- New file `backend/core/child_env.py`, with AIFactory's function names
  (reference: AIFactory `origin/dev` `apps/backend/core/auth.py` lines 100-240
  and `core/child_env.py` `child_env`). No git pins.
  - `is_denied_env_key(name)`: exact set (AIFactory's `_AGENT_ENV_DENY_EXACT`
    plus `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `NIX_CONFIG`,
    `NIX_ACCESS_TOKENS`, `PFACTORY_MCP_KEY`, `PFACTORY_API_TOKEN_FILE` and the other
    `PFACTORY_*` token and key names the backend reads; grep for them) or pattern
    `SECRET|PASSWORD|PRIVATE_KEY|CREDENTIAL|(?:^|_)KMS|PASSPHRASE|TRUSTED_PLAN_KEY|SIGNING_KEY`
    (case-insensitive). Exact match is on `name.upper()`.
  - `is_agent_env_allowed(name)`: AIFactory's `_AGENT_ENV_ALLOW` pattern
    (fullmatch) plus `HOME|XDG_\w+_HOME|CODEX_HOME`, or a name listed in
    `PFACTORY_AGENT_ENV_EXTRA` (comma-separated); never a denied name. It does not
    include the Claude auth names.
  - `CLAUDE_KEEP`: `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`,
    `ANTHROPIC_BASE_URL` and `core.auth.SDK_ENV_VARS`.
  - `get_agent_env_blanks()`: `{name: ""}` for each host variable that is
    neither allowed nor in `CLAUDE_KEEP`.
  - `child_env(keep=(), extra=None)`: allowed host variables, then `keep`
    names that are set, then `extra`. No credential by default.
  - `without_host_secrets(env)`: a copy of `env` without entries whose
    non-empty value equals the value of a denied host variable.
  - Dropped names that are not denied are logged once per process, names only.
- Credentials kept per child: Claude CLI (SDK and `claude --print`) keeps
  `CLAUDE_KEEP`; codex keeps `OPENAI_API_KEY`, `CODEX_API_KEY`; gemini keeps
  `GEMINI_API_KEY`, `GOOGLE_API_KEY`; copilot keeps `COPILOT_GITHUB_TOKEN`;
  the executor shell and every repository-code child keep none.
- Not changed: the first-party runner env (`make_subprocess_env` and its
  `run.py`, changelog and PR-review callers), git and `gh` calls, the docker
  and Kubernetes runners, the PTY shell, the project `.env` loading.
- Tests assert on sentinel values, not names.

## Steps

1. `backend/core/child_env.py` (new) and `tests/test_child_env.py` (new): the
   helper as decided above, with unit tests: each of `APP_API_TOKEN`,
   `APP_JWT_SECRET`, `DATABASE_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`,
   `GITHUB_TOKEN`, `GH_TOKEN`, `NIX_CONFIG`, `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY_2`
   and `CLAUDE_CODE_OAUTH_TOKEN` set to a distinct sentinel: none of the
   values is in `child_env()`; the opt-in cannot restore a denied name but
   restores a plain one; `keep` restores; blanks leave `CLAUDE_KEEP` alone and
   blank the rest; `without_host_secrets` drops a renamed copy.
   → verify by `python -m pytest tests/test_child_env.py -q`
   Traps: tests add `apps/backend` to `sys.path` like
   `tests/test_provider_auth_policy.py`. Ruff line length 100.
2. SDK and MCP wiring.
   - `backend/core/client.py:918` (`"env": sdk_env`): pass
     `{**get_agent_env_blanks(), **sdk_env}` so explicit values (auth, effort
     level, broker credentials from line 555) win over blanks.
   - `backend/core/simple_client.py:101`: same.
   - `backend/core/client.py:833`: after `build_server_config`, if the config
     has an `env` dict, replace it with `without_host_secrets(env)` (drop the
     key when it ends up empty).
   - `backend/core/client.py:986-989`: correct the stale "Caller is responsible
     for env scrubbing" comment.
   → verify by step 6 tests. Traps: do not reorder `inject_task_credentials`.
3. Backend agent runners.
   - `backend/providers/codex_agentic.py:181`:
     `scrub_api_keys(child_env(keep=("OPENAI_API_KEY", "CODEX_API_KEY")), "codex")`.
   - `backend/providers/gemini_agentic.py:170`: same with
     `("GEMINI_API_KEY", "GOOGLE_API_KEY")` and `"gemini"`.
   - `backend/providers/copilot_agentic.py:159`: add
     `env=child_env(keep=("COPILOT_GITHUB_TOKEN",))`.
   - `backend/tools/executor.py:339` and `:440`: add `env=child_env()`.
   Traps: `CODEX_HOME` set later in `_build_subprocess_env` must survive.
4. Web-server agent CLIs. In `web-server/server/utils/subprocess_env.py` add
   the backend dir to `sys.path` (as AIFactory's copy does) and re-export
   `child_env` and `CLAUDE_KEEP`. Replace `make_subprocess_env()` with:
   - `services/clarification_service.py:168`, `services/insights_service.py:509`,
     `services/insights_providers/claude_provider.py:183`: `child_env(keep=CLAUDE_KEEP)`.
   - `services/insights_providers/codex_provider.py:85`:
     `child_env(keep=("OPENAI_API_KEY", "CODEX_API_KEY"))`.
   - `services/insights_providers/gemini_provider.py:96`:
     `child_env(keep=("GEMINI_API_KEY", "GOOGLE_API_KEY"))`.
   Traps: `make_subprocess_env` itself and its runner callers
   (`agent_service.py`, `changelog_service.py`, `pr_review_service.py`) stay as
   they are. Other teams are editing `agent_service.py`; do not touch it.
5. Repository-code children.
   - `backend/agents/preflight_static.py:215`: `env = child_env()` in place of
     `os.environ.copy()`; the `PYTHONPATH` logic below it then starts from an
     empty value.
   - `backend/services/orchestrator.py:301`, `:337`, `:371`, `:397`, `:409`:
     add `env=child_env()`.
6. `tests/test_agent_env_sites.py` (new): for each site in steps 2-5, seed the
   sentinels, call the site with the spawn function patched to capture `env`
   (for the SDK sites, capture `ClaudeAgentOptions` and compute
   `{**os.environ, **options.env}`), and assert no sentinel value of a
   control-plane secret is present, and that the Claude token is absent from
   the children marked "none". One real-process test: the executor's bash
   tool runs `env` and no sentinel is in the output.
   → verify by `python -m pytest tests/test_agent_env_sites.py -q`
   Traps: a site that is impractical to call in isolation gets a narrower
   test of the env expression, and the plan records it as a deviation.

## Tests

- `cd tests && python -m pytest test_child_env.py test_agent_env_sites.py -q`
  passes (use the backend venv).
- The existing tests of the touched modules pass:
  `python -m pytest tests -q -k "provider or executor or client or insights or clarification or preflight or evaluator or orchestrator or mcp"`.
- `python scripts/ratchet_lint.py` reports no new findings for touched files.

## Rollback

Revert the squash commit. The helper is additive; no data or schema changes.

## Deviations

- Step 1: `child_env` also drops allowed names that end in `_KEY` or `_TOKEN`
  (AIFactory's `_CREDENTIAL_NAME` rule) instead of excluding `CLAUDE_KEEP`
  by name. `CLAUDE_CODE_OAUTH_TOKEN` matches the `CLAUDE_CODE_*` allow
  pattern, and excluding the whole keep set also dropped `NO_PROXY`. An
  opted-in token name still reaches the Claude CLI (blanks) but not a child
  we spawn, unless the site names it in `keep`.
- Step 5: the `docker compose` calls in `services/orchestrator.py` keep
  `DOCKER_HOST`, `DOCKER_TLS_VERIFY`, `DOCKER_CERT_PATH` and
  `DOCKER_CONTEXT` (addresses and paths, not credentials) so a remote or
  TLS docker daemon still works; the project's startup command keeps nothing.
- Step 6: the codex site is tested through `_build_subprocess_env()`, where
  its env is built. The `DATABASE_URL` sentinel is a sqlite URL, because the
  web-server modules refuse to import with an unparsable one.
- Final review: `core/mcp_credentials.py` `_probe_github` now reads the
  dedicated `GITHUB_PERSONAL_ACCESS_TOKEN` before the pod `GITHUB_TOKEN`
  (an operator `tokenEnv` still comes first). Without that, the dedicated
  token the spec asks operators to set was never used while the pod token
  existed. `get_agent_env_blanks` also blanks credential-shaped names the
  allow pattern admits (`NIX_GITHUB_TOKEN`), and `without_host_secrets`
  ignores values shorter than 8 characters.
