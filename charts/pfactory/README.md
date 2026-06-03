# AIFactory Helm chart

Production Helm chart for self-hosted enterprise deployments of
AIFactory (Epic #26). PSS-restricted by default; NetworkPolicy-
enforced egress; integrates with the four major external-secret
backends (Vault, AWS Secrets Manager, Azure Key Vault, GCP Secrets
Manager).

## Quick start (POC mode — bundled Postgres)

```bash
helm dep update
helm install aifactory ./charts/aifactory \
  --set postgres.bundled=true \
  --set image.repository=ghcr.io/olafkfreund/aifactory \
  --set image.tag=1.0.0
```

## Production install (external Postgres + ExternalSecrets + OIDC)

See [guides/deployment/helm-install.md](../../guides/deployment/helm-install.md)
for the full operator runbook — per-cloud setup, secret seeding,
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
| `kms` | At-rest encryption backend (Epic #26 P2). |
| `global.customCABundle` | TLS-intercepting proxy support. |

## Requirements

- Kubernetes 1.27+
- Helm 3.16+
- (optional) `cloudnative-pg` chart repo (when `postgres.bundled=true`)
- (optional) External Secrets Operator installed cluster-wide
  (when `externalSecrets.enabled=true`)

## License

Dual-licensed: MIT OR GPL-3.0 — see [LICENSE](../../LICENSE).
