# PFactory Helm chart

Production Helm chart for self-hosted enterprise deployments of
PFactory (Epic #26). PSS-restricted by default; NetworkPolicy-
enforced egress; integrates with the four major external-secret
backends (Vault, AWS Secrets Manager, Azure Key Vault, GCP Secrets
Manager).

## Quick start (POC mode — bundled Postgres)

```bash
helm dep update
helm install pfactory ./charts/pfactory \
  --set postgres.bundled=true \
  --set image.repository=ghcr.io/dataseeek/pfactory \
  --set image.tag=1.0.0
```

## Production install (external Postgres + ExternalSecrets + OIDC)

See [guides/shipping.md](../../guides/shipping.md)
for the full operator runbook — image build, secret seeding,
migration job mode, customCABundle for TLS-intercepting proxies.

## Values surface

`values.yaml` is the primary config surface. Schema-validated via
`values.schema.json` (so `helm lint --strict` catches typos).

| Section | Purpose |
| --- | --- |
| `image` | Container image reference (override repo for mirror registries). |
| `replicaCount` | Pinned to 1 for v1.0 (WebSocket fan-out limitation). |
| `resources` | CPU/memory requests + limits. |
| `podSecurityContext` / `containerSecurityContext` | PSS-restricted defaults. |
| `service` / `ingress` | Network exposure. |
| `serviceAccount` / `rbac` | Pod identity. |
| `networkPolicy` | Default-deny + 443 egress allowlist. |
| `migrations` | Alembic Job mode (autoApply=false in prod). |
| `postgres` | External (default) or bundled CNPG sub-chart. |
| `externalSecrets` | One of: vault / aws-sm / azure-kv / gcp-sm. |
| `oidc` | OIDC SSO settings (Epic #26 P3). |
| `kms` | At-rest encryption backend (Epic #26 P2 — see below). |
| `global.customCABundle` | TLS-intercepting proxy support. |

## At-rest encryption (`kms`)

**User story.** As an operator moving off the local Fernet key, I want
`kms.backend=aws_kms` to either work or tell me what is missing — never to give
me a running pod that quietly writes credentials in plaintext (AIFactory#1290).

`kms.backend` is one of five values. Each needs something before the pod can
encrypt; selecting one without it fails `helm template` with the name of the
value you left out.

| `kms.backend` | You must also set | Where it lands |
| --- | --- | --- |
| `fernet` (default) | `kms.fernetKeyRef` (name/key of a Secret) | Secret ref |
| `aws_kms` | `kms.awsKmsKeyId` (ARN, id, or alias) | ConfigMap |
| `vault_transit` | `kms.vaultAddr` + `kms.vaultTokenRef.name` (Secret holding a token with transit encrypt/decrypt caps); `kms.vaultTransitKey` defaults to `pfactory-root` | URL in ConfigMap, token via Secret ref |
| `azure_kv` | `kms.azureKeyvaultUrl` + `kms.azureKeyvaultKey` (credentials come from the pod's managed identity via `DefaultAzureCredential`) | ConfigMap |
| `gcp_kms` | `kms.gcpKmsKeyName` (`projects/…/cryptoKeys/…`); credentials come from Workload Identity | ConfigMap |

Key **identifiers** are not secrets and ride the ConfigMap. The only two real
credentials — the Fernet key and the Vault token — come from Secret refs. Never
put either in a values file: it ends up in `helm get values` and in whatever git
repo holds the release.

**Unset behaviour.** Leave `kms` alone and you get `fernet` with the
`pfactory-kms` Secret, which is what the default install provisions.

**If the key does not actually arrive** (empty Secret, a values file setting it
to `""`, an unreachable KMS) the chart cannot see it — so the app refuses to
start when a *non-default* backend was selected and cannot be constructed. The
unconfigured default keeps its documented behaviour: it degrades to plaintext
with a one-time warning, because there nothing was ever provisioned to begin
with.

## Requirements

- Kubernetes 1.27+
- Helm 3.16+
- (optional) `cloudnative-pg` chart repo (when `postgres.bundled=true`)
- (optional) External Secrets Operator installed cluster-wide
  (when `externalSecrets.enabled=true`)

## License

Dual-licensed: MIT OR GPL-3.0 — see [LICENSE](../../LICENSE).
