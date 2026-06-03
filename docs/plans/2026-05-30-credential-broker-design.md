# Credential Broker & Pluggable Secrets Backends — Design

> Status: Approved design · Created: 2026-05-30 · Epic: (to be created, links to #33)
> Owner: DataSeek Team

## Summary

Give PFactory's agents (Planner / Gen-Functional / Evaluator / Triager) and its
MCP tools a **secure, declarative way to authenticate to external cloud
environments** — Google Cloud, AWS, Azure, Kubernetes (kubeconfig), and generic
API endpoints — by resolving credentials from pluggable backends (Azure Key
Vault, AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) or local
encrypted files (sops / age / agenix), **without baking secrets into the repo**.

Credentials are resolved **on demand**, materialised **ephemerally** (env vars +
0600 files in a per-task scratch dir, wiped on task end), and gated by an
**explicit per-project egress opt-in** that produces an honest **egress
manifest** — mirroring the existing `byo_llm` egress-classification posture.

### Decisions (locked with the product owner)

| # | Decision | Choice |
|---|----------|--------|
| D1 | Primary v1 consumer | **Agents → cloud/MCP** (vault-backed cloud auth for the agents). Sandbox-test injection is a fast-follow. |
| D2 | Backends in scope | env + local file (sops/age/agenix), HashiCorp Vault, Azure Key Vault, AWS Secrets Manager, GCP Secret Manager — each its own child issue. |
| D3 | Credential lifetime | **Pass-through** in v1 (resolve → inject → wipe). OIDC / workload-identity federation is a fast-follow. |
| D4 | Egress safety | **Explicit per-project opt-in + generated egress manifest**; default resolves **no** cloud creds; honest-egress badge. |

## Background: what already exists (reuse, don't reinvent)

- **`apps/backend/core/mcp_credentials.py`** — a per-provider credential
  *resolution chain* already covering **Kubernetes** (operator kubeconfig →
  `KUBECONFIG` → `~/.kube/config` → in-cluster service-account token), **AWS**
  (profile → env keys → `~/.aws/credentials` → IRSA), **Azure** (service
  principal → `~/.azure` → Managed Identity), **GCP**
  (`GOOGLE_APPLICATION_CREDENTIALS` → ADC), plus GitHub / GitLab / Azure DevOps.
  Operator config at `~/.pfactory/mcp-credentials.json` (0600); cheap
  non-validating probes returning `CredentialStatus(available, source, env_vars)`.
  **The broker extends this spine — it adds a "fetch from backend" head to each
  chain.**
- **`apps/backend/providers/factory.py`** — two-tier registry + alias map +
  lazy-import `_instantiate()`. The `SecretsBackend` factory mirrors it.
- **`apps/backend/phase_config.py`** — `infer_provider_from_model()` /
  `strip_provider_prefix()` prefix routing, mirrored by `infer_backend_from_ref()`.
- **`apps/backend/byo_llm.py`** — `EgressClass` (LOCAL / SELF_HOSTED /
  MANAGED_CLOUD), `egress_report()`, host classification. Shared for credential
  destinations.
- **`apps/backend/pfactory_yml/{schema,secrets}.py`** — `.pfactory.yml` auth
  models (`BearerAuth` / `ServiceAccountAuth` / `MtlsAuth` …) that store env-var
  *names* only, with `resolve_env_var()` / `resolve_auth_env_vars()`. Extended
  with a `credentials:` / `egress:` block.
- **`EncryptedString` ORM type** (`apps/web-server/server/crypto/encrypted_string.py`,
  imported as `_EncryptedString` in `server/database/models.py`) — encrypts
  secret columns at rest (LargeBinary); web-server CRUD mirrors
  `routes/llm_endpoints.py`.
- **`apps/backend/tools/runners/docker_runner.py`** — `-e` env injection +
  `extra_env`; default is `--network=none`, the **api** lane opts into
  `network="host"` and the **browser** lane reaches services via an AppRuntime
  wrapper. This is the seam for the fast-follow sandbox injection.

### The gaps this design fills

1. No **pluggable external vault** backend — today everything is env-var-name or
   local-file based.
2. Agents have no first-class way to **obtain cloud credentials** from a vault at
   runtime (the mcp_credentials chain stops at local files / ambient identity).
3. No **honest egress accounting** for credential destinations.
4. (Fast-follow) Secrets resolved for prompt context are never injected into the
   **test sandbox** at execution time.

## Architecture (v1)

```
                ┌─────────────────────────────────────────────┐
   agent /      │            CredentialBroker                  │
   MCP tool ───►│  resolve_cloud("gcp"|"aws"|"azure"|"k8s")    │
                │  resolve_ref("vault:secret/...#token")        │
                └───────────────┬─────────────────────────────┘
                                │ (1) read config: operator + .pfactory.yml
                                │ (2) egress gate (opt-in? build manifest)
                                ▼
                ┌───────────────────────────────┐   infer_backend_from_ref()
                │      get_secrets_backend()     │◄── "vault:" "azurekv:" "aws-sm:"
                │   (factory, mirrors providers) │    "gcp-sm:" "sops:" "env:"
                └───────────────┬───────────────┘
        ┌───────────┬───────────┼───────────┬───────────┬──────────┐
        ▼           ▼           ▼           ▼           ▼          ▼
      env        sops/age     vault     azure_kv    aws_sm     gcp_sm
   (existing)   (local)      (hvac)   (azure-id)  (boto3)   (google-cloud)
                                │
                                ▼  materialise (ephemeral, 0600, per-task, wiped)
                  env vars  +  cred files (kubeconfig, ADC json) in scratch
                                │
                                ▼  threaded into core/client.py agent env + MCP env
```

### New package: `apps/backend/pfactory_secrets/`

> Named `pfactory_secrets` (not `secrets`) to avoid shadowing Python's stdlib
> `secrets` module, which the backend uses for token generation.

| Module | Responsibility |
|--------|----------------|
| `__init__.py` | `SecretsBackend` ABC (`resolve(ref) -> SecretValue`, `available() -> bool`, `egress_class() -> EgressClass`); `SecretRef`, `SecretValue` dataclasses |
| `factory.py` | `get_secrets_backend(name, **kwargs)` + `_BACKEND_REGISTRY` + alias map + **lazy** `_instantiate()` so a missing `boto3`/`hvac`/azure SDK never breaks startup |
| `refs.py` | `infer_backend_from_ref()` / `parse_ref()` (mirrors `infer_provider_from_model`) |
| `backends/env.py` | env-var name → value (wraps existing `resolve_env_var`) |
| `backends/localfile.py` | sops / age / agenix decrypt of local files |
| `backends/vault.py` | HashiCorp Vault (hvac) |
| `backends/azure_keyvault.py` | azure-identity + azure-keyvault-secrets |
| `backends/aws_secrets_manager.py` | boto3 |
| `backends/gcp_secret_manager.py` | google-cloud-secret-manager |
| `broker.py` | `CredentialBroker(project_dir, spec_dir)`: `resolve_cloud()` / `resolve_ref()`; ephemeral materialise + per-task wipe (`atexit` + `close()`) |
| `egress.py` | destination classification + `build_manifest()` + badge |
| `redaction.py` | reuse `security/scan_secrets.py` regexes to scrub resolved values from logs |
| `cli.py` | `python -m pfactory_secrets.cli audit|resolve|doctor` |

### Credential reference syntax (mirrors the model-string prefix)

```
env:STAGING_API_TOKEN
sops:secrets.enc.yaml#api_token
agenix:staging-token.age
vault:secret/data/pfactory/staging#api_token
azurekv://my-vault/STAGING-API-TOKEN
aws-sm://staging/api#token
gcp-sm://my-project/staging-api-token[/version]
```

`infer_backend_from_ref()` routes by scheme/prefix exactly like
`infer_provider_from_model()` routes by model string.

### Configuration surfaces

- **Operator** — `~/.pfactory/credentials.json` (0600), extends
  `mcp-credentials.json`: named credential sets mapping cloud providers →
  backend refs + backend connection config (vault addr, KV name, AWS region…).
- **Per-project** — new `.pfactory.yml` blocks:
  ```yaml
  credentials:
    gcp: { ref: "gcp-sm://proj/pfactory-sa", as: GOOGLE_APPLICATION_CREDENTIALS, kind: file }
    staging_api: { ref: "vault:secret/data/staging#token", as: STAGING_API_TOKEN }
  egress:
    enabled: true          # default false -> no cloud creds resolved
    destinations:
      - { name: staging-api, host: api.staging.example.com }
      - { name: gke,        host: "*.googleapis.com" }
  ```
- **DB-backed (web-server, optional)** — backend connection configs stored
  encrypted via `_EncryptedString`, CRUD mirroring `routes/llm_endpoints.py`.

### Security invariants

- Resolved secret **values are never written to disk unencrypted**. Ephemeral
  cred files (kubeconfig, ADC JSON) live in a per-task scratch dir at mode 0600
  and are wiped on task completion and on crash (`atexit`/`finally`).
- Default = **no cloud creds resolved**; resolution requires `egress.enabled`.
- All resolved values are **redacted from logs** via the existing secret-scan
  regexes (`security/scan_secrets.py`).
- Backend connection configs are **encrypted at rest** (`_EncryptedString`).
- Backends import their SDKs **lazily** — absence of a cloud SDK degrades that
  one backend to "unavailable", never breaks the process.

## Epic & child issues

**Epic:** *Credential Broker & pluggable Secrets Backends — authenticate agents
to cloud environments* (links to epic #33).

| # | Child issue | Notes |
|---|-------------|-------|
| 1 | **Foundation** — `pfactory_secrets/` package: `SecretsBackend` ABC + factory + `refs.py` + `env` + `localfile` backends + unit tests | No cloud SDK. AC: package named `pfactory_secrets` (no stdlib `secrets` shadow); `parse_ref()` does explicit **per-scheme** parsing (mixes `scheme:path#frag` and `scheme://authority/name`), not one regex |
| 2 | **sops/age/agenix local-file backend** | NixOS / air-gapped friendly |
| 3 | **CredentialBroker + cloud chain** — `broker.py` extending `mcp_credentials.py`; `resolve_cloud()` for gcp/aws/azure/k8s; ephemeral materialise + wipe; wire into `core/client.py` | Core of v1 |
| 4 | **HashiCorp Vault backend** (hvac) | |
| 5 | **Azure Key Vault backend** (azure-identity + azure-keyvault-secrets) | |
| 6 | **AWS Secrets Manager backend** (boto3) | |
| 7 | **GCP Secret Manager backend** (google-cloud-secret-manager) | |
| 8 | **Egress safety** — opt-in gate + `egress.py` manifest + honest-egress badge + log redaction + `secrets.cli audit` | |
| 9 | **Config surfaces** — `.pfactory.yml` `credentials:`/`egress:` schema + operator-config schema + web-server CRUD (encrypted) | |
| 10 | **Docs** — `guides/credentials.md`, CHANGELOG, decision-log DEC entry, roadmap update | |
| 11 | **(Fast-follow) Sandbox-test credential injection** — `docker_runner.py` `-v` mounts + `secrets_env` through `_resolve_runner_fn`; per-lane gating | Default `--network=none`/no-creds preserved |
| 12 | **(Fast-follow) OIDC / workload-identity federation** — STS AssumeRoleWithWebIdentity, GCP WIF, Azure federated tokens | Short-lived scoped creds |

## Files to add / modify

- **New:** `apps/backend/pfactory_secrets/` (package above) + `tests/test_secrets_*.py`.
- **Modify:** `core/mcp_credentials.py` (backend-fetch head of each chain),
  `core/client.py` (inject broker-resolved env for agents),
  `pfactory_yml/schema.py` (+`credentials:`/`egress:`), `byo_llm.py` (share
  egress classification), web-server `routes/` (+credentials CRUD).
- **Mirror for pattern (read-only):** `providers/factory.py`, `phase_config.py`.

## Testing strategy

- **Unit:** per-backend `resolve()` with **mocked SDK clients** (no live cloud);
  ref-parsing/routing tests mirroring `tests/test_studio_routing.py`; egress
  classification + manifest tests; redaction tests; ephemeral-wipe test
  (file 0600 then absent after `close()`).
- **Factory:** `get_secrets_backend("vault"|"azurekv"|…)` returns the right
  class; missing SDK import is lazy and surfaces as `available() == False`.
- **Broker:** `resolve_cloud("gcp")` with a fake backend yields env vars + an ADC
  file path in scratch; egress gate off → returns unavailable, emits nothing;
  egress gate on → manifest lists every secret→destination with a badge.
- **End-to-end (manual):** a demo agent task with a local sops file + egress
  opt-in resolves a fake GCP cred; an MCP tool reports the env reachable; the
  triage/portal shows the egress manifest.
- **No regression:** existing `mcp_credentials`, provider-factory, and
  `.pfactory.yml` parsing tests stay green.

## Out of scope (v1)

- Minting short-lived/federated credentials (issue #12, fast-follow).
- Injecting credentials into the test sandbox (issue #11, fast-follow).
- Encrypting the whole `data.db` at rest (field-level `_EncryptedString` only).
- Automatic secret rotation policy.
