# Credential Broker — authenticate agents to cloud environments

> PFactory's agents can authenticate to **Google Cloud, AWS, Azure, Kubernetes**
> and generic APIs using credentials pulled from a **vault** (Azure Key Vault,
> AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) or **local encrypted
> files** (sops / age / agenix) — never baked into the repo. Resolution is
> off by default and gated by an explicit per-project **egress opt-in** with an
> honest egress manifest.
>
> Epic [#62](https://github.com/olafkfreund/PFactory/issues/62). Design:
> `docs/plans/2026-05-30-credential-broker-design.md`.

## TL;DR

```bash
# 1. Reference secrets by a backend-prefixed ref (no values in the repo)
#      env:NAME · sops:file#key · agenix:x.age · vault:path#field
#      azurekv://vault/name · aws-sm://name#field · gcp-sm://proj/secret[/ver]

# 2. Opt into egress in .pfactory.yml (default: OFF -> no cloud creds resolved)
cat >> .pfactory.yml <<'YAML'
egress:
  enabled: true
  destinations:
    - { name: staging-api, host: api.staging.example.com }
credentials:
  gcp:         { ref: "gcp-sm://my-proj/pfactory-sa", as: GOOGLE_APPLICATION_CREDENTIALS, kind: file }
  staging_api: { ref: "vault:secret/data/staging#token", as: STAGING_API_TOKEN }
YAML

# 3. See exactly what would leave your network (secret-free)
python -m pfactory_secrets.cli audit .

# 4. Run the pipeline as usual — the agent inherits the resolved env + cred files.
```

## Secret reference syntax

| Backend | Ref form | Resolves via |
|---|---|---|
| Environment | `env:NAME` | the named env var |
| Local file (plain) | `file:/path[#field]` | read file (optionally a `key: value` field) |
| sops | `sops:file.enc.yaml#key` | `sops -d` |
| age / agenix | `age:f.age` · `agenix:x.age` | `age`/`rage -d -i <identity>` |
| HashiCorp Vault | `vault:secret/data/app#field` | `hvac` (KV-v1/v2), `VAULT_ADDR`/`VAULT_TOKEN` |
| Azure Key Vault | `azurekv://vault-name/secret` | `azure-identity` + `azure-keyvault-secrets` |
| AWS Secrets Manager | `aws-sm://name#json-field` | `boto3`, region from `AWS_REGION` |
| GCP Secret Manager | `gcp-sm://project/secret[/version]` | `google-cloud-secret-manager` (ADC) |

Routing is driven entirely by the ref scheme (`pfactory_secrets.refs.infer_backend_from_ref`).
Cloud SDKs are imported **lazily** — an absent package makes only that backend
unavailable, never breaks PFactory.

## Auth & age identity

Cloud backends reuse each SDK's standard credential chain (the same ambient
sources `core/mcp_credentials.py` probes): Vault token/AppRole, Azure
`DefaultAzureCredential` (SP / Managed Identity / CLI), the boto3 chain (env /
`~/.aws` / profile / IRSA), and GCP ADC. For `age`/`agenix`, the identity is
found from `PFACTORY_AGE_IDENTITY` / `SOPS_AGE_KEY_FILE` / `AGE_IDENTITY_FILE`
or `~/.config/sops/age/keys.txt`.

## Configuration surfaces

- **Per-project** `.pfactory.yml` — `egress:` (opt-in + declared destinations)
  and `credentials:` (named refs → env var, `kind: env|file`). `kind: file`
  writes the value to a **0600** file in a per-task scratch dir and sets the env
  var to that path (kubeconfig, GCP ADC json).
- **Operator** `~/.pfactory/credentials.json` (chmod 600) — host-wide config
  read by the broker, with two blocks (a looser file mode still loads, with a
  warning). Values are always *references*, never secrets:
  ```json
  {
    "cloud": {
      "gcp": { "ref": "gcp-sm://proj/sa", "as": "GOOGLE_APPLICATION_CREDENTIALS", "kind": "file" }
    },
    "credentials": {
      "staging-db": { "ref": "vault:secret/data/staging/db#url", "as": "DATABASE_URL" }
    }
  }
  ```
  - `cloud` — provider → backend ref; the broker's cloud-provider fetch head.
  - `credentials` — named sets → backend ref, the host-wide analogue of the
    per-project `.pfactory.yml` `credentials:` block. On a name collision the
    per-project entry wins. Schema/loader: `pfactory_secrets/operator_config.py`.

## Egress posture (honest by default)

- **Default: OFF.** With no `egress.enabled`, the broker resolves **no** cloud
  credentials. Local backends (`env`, `localfile`) never egress.
- **`python -m pfactory_secrets.cli audit`** prints a secret-free manifest:
  every credential, its backend, its egress class + badge (LOCAL /
  SELF-HOSTED / MANAGED CLOUD), and the declared destinations.
- Resolved secret **values are never written to disk unencrypted** (file creds
  are 0600 in a per-task scratch dir, wiped on task end) and are **redacted from
  logs** (`pfactory_secrets.redaction`).

## Tooling

```bash
python -m pfactory_secrets.cli audit [project_dir] [--json]  # egress manifest
python -m pfactory_secrets.cli doctor                        # backend availability
python -m pfactory_secrets.cli resolve <ref> [--allow-egress] # redacted resolve
```

## Programmatic API

```python
from pfactory_secrets.broker import CredentialBroker

with CredentialBroker(project_dir, spec_dir, egress_allowed=True) as broker:
    token = broker.resolve_ref("vault:secret/data/app#token").value
    status = broker.resolve_cloud("gcp")   # CredentialStatus(available, source, env_vars)
    broker.apply_to_env(os.environ)        # merge resolved env for a subprocess
# cred files wiped on exit
```

## Sandbox credential injection (#73)

Network-enabled lanes (api / integration) can authenticate to the SUT/cloud
**inside** the Docker sandbox. Strictly gated:

- The **unit** lane runs `--network=none` and receives **no** credentials.
- A network-enabled lane gets broker-resolved creds **only when egress is
  opted in** — cloud tokens as env vars, and a kubeconfig (when materialised)
  bind-mounted **read-only** at `/root/.kube/config`.
- Materialised secret files are **wiped after the run**.

Seam: `tools/runners/sandbox_credentials.resolve_sandbox_credentials(project_dir,
spec_dir, network)` → `DockerRunner.run(..., secret_files=…)`; wired into the
Evaluator's pytest runner for the api lane.

## Workload-identity federation (#74)

Instead of a long-lived secret, mint **short-lived scoped** creds from an OIDC
token. Configure a `wif` block in `~/.pfactory/credentials.json`:

```json
{ "wif": { "aws": { "role_arn": "arn:aws:iam::123:role/pfactory",
                    "token_file": "/var/run/secrets/oidc/token",
                    "duration_seconds": 3600 } } }
```

`resolve_cloud("aws")` then runs STS `AssumeRoleWithWebIdentity` and returns
short-lived `AWS_ACCESS_KEY_ID`/`SECRET`/`SESSION_TOKEN`. The broker caches them
with their TTL and **re-mints automatically** as they near expiry. GCP Workload
Identity Federation and Azure federated tokens are routed but not yet
implemented. Module: `pfactory_secrets/wif.py`.
