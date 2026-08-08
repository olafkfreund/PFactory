# Shipping PFactory

Operator runbook for building, packaging, and deploying PFactory. Covers
the three deliverables that ship together:

1. The **application image** (`Dockerfile` → `ghcr.io/dataseeek/pfactory`).
2. The **runner images** (`docker/pfactory-runner-*` — per-framework test
   sandboxes referenced by `frameworks/*/descriptor.yaml`).
3. The **Helm chart** (`charts/pfactory/`) for Kubernetes installs, plus
   the `docker-compose.yml` path for single-host installs.

For version bumping, changelog, git tags, and GitHub releases see
[RELEASE.md](../RELEASE.md). This guide picks up from a tagged source tree
and turns it into running infrastructure.

---

## 1. Build images

### Application image

The root `Dockerfile` is a two-stage Chainguard build: stage 1 builds the
React frontend into `apps/web-server/static/`, stage 2 is the distroless
Python runtime. It runs as the `nonroot` user (uid 65532), exposes port
**3114**, and serves the FastAPI web-server (`server.main`) — including the
planning routes (`/api/plan/*`) added in `server/routes/plan_*.py`, which
need no extra build args (in-memory, `fastapi` + `python-multipart` are
already in `apps/web-server/requirements.txt`).

```bash
# Default (bank-pilot) image — no rmux binary bundled
docker build -t ghcr.io/dataseeek/pfactory:1.0.0 .

# Optional dev/demo image with the rmux Live Agent Console (Epic #44 R3)
docker build --build-arg WITH_RMUX=true \
  -t ghcr.io/dataseeek/pfactory:1.0.0-rmux .

docker push ghcr.io/dataseeek/pfactory:1.0.0
```

Validate the Dockerfile without a full build:

```bash
docker build --check .
```

### Runner images

Each test framework runs in its own sandbox image. **You normally do not build
these.** `.github/workflows/runner-images.yml` builds, smoke-tests, publishes and
signs all six on every push to `main`, and the runner resolves bare
`pfactory-runner-*` tags to that published copy:

```
ghcr.io/olafkfreund/pfactory-runner-{pytest,jest,vitest,playwright,cypress,cloud}:latest
ghcr.io/olafkfreund/pfactory-runner-...:<commit-sha>     # what :latest points at
```

Building them by hand used to be the only way to get them, and it is what made
the lanes untraceable: nothing tied the tag a lane executed to a commit (#449).

Verify what you are about to run:

```bash
cosign verify \
  --certificate-identity-regexp \
  '^https://github\.com/olafkfreund/PFactory/\.github/workflows/runner-images\.yml@refs/heads/main$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/olafkfreund/pfactory-runner-pytest:latest
```

Build locally only when you are **changing** a runner image. Set
`PFACTORY_RUNNER_REGISTRY=` (empty) so bare tags resolve to your build instead of
the published one, then (image names must match `runtime.image` in the matching
descriptor):

```bash
docker build -f docker/pfactory-runner-pytest/Dockerfile \
  -t pfactory-runner-pytest:latest      docker/pfactory-runner-pytest
docker build -f docker/pfactory-runner-jest/Dockerfile \
  -t pfactory-runner-jest:latest        docker/pfactory-runner-jest
docker build -f docker/pfactory-runner-vitest/Dockerfile \
  -t pfactory-runner-vitest:latest      docker/pfactory-runner-vitest
docker build -f docker/pfactory-runner-playwright/Dockerfile \
  -t pfactory-runner-playwright:latest  docker/pfactory-runner-playwright
docker build -f docker/pfactory-runner-cypress/Dockerfile \
  -t pfactory-runner-cypress:latest     docker/pfactory-runner-cypress
docker build -f docker/pfactory-runner-cloud/Dockerfile \
  -t pfactory-runner-cloud:latest       docker/pfactory-runner-cloud
```

Mirror runner images into your private registry the same way as the app
image if your cluster has no egress to public registries.

---

## 2. Single-host install (docker-compose)

For a developer laptop or a single VM. Not the production surface — use the
Helm chart for clusters. See [ContainerAPP.md](../ContainerAPP.md) for the
full quick-reference.

```bash
docker compose build
docker compose up -d

# Retrieve the auto-generated API token (first run writes it here):
docker exec pfactory cat /home/nonroot/.pfactory/.token
```

- URL: `http://localhost:${HOST_PORT:-3114}`
- Data (token, projects DB) persists in `${PFACTORY_DATA_DIR:-./data}`,
  mounted at `/home/nonroot/.pfactory`.

---

## 3. Kubernetes install (Helm)

The chart is `charts/pfactory/`. Defaults are PSS-restricted (non-root,
read-only root fs, dropped capabilities), NetworkPolicy is on (default-deny
+ 443 egress allowlist), and `replicaCount` is pinned to 1 for v1.0.

### POC mode (bundled Postgres)

```bash
helm dep update charts/pfactory
helm install pfactory ./charts/pfactory \
  --namespace pfactory --create-namespace \
  --set postgres.bundled=true \
  --set image.repository=ghcr.io/dataseeek/pfactory \
  --set image.tag=1.0.0
```

### Production mode (external Postgres + ExternalSecrets + OIDC + Ingress)

```bash
helm install pfactory ./charts/pfactory \
  --namespace pfactory --create-namespace \
  --set image.repository=ghcr.io/dataseeek/pfactory \
  --set image.tag=1.0.0 \
  --set postgres.bundled=false \
  --set externalSecrets.enabled=true \
  --set externalSecrets.backend=vault \
  --set oidc.enabled=true \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.hosts[0].host=pfactory.example.com
```

### Required / important values

| Value | Required? | Notes |
| --- | --- | --- |
| `image.repository` / `image.tag` | yes | `tag` defaults to chart `appVersion` (1.0.0) if unset. Point `repository` at your mirror registry if no public egress. |
| `ingress.enabled` + `ingress.hosts[0].host` | for public access | Set the host to `pfactory.<your-domain>` and wire `ingress.className` to your controller (`nginx`, `alb`, …). TLS via `ingress.tls`. |
| `postgres.bundled` | yes (pick one) | `true` installs the CNPG sub-chart (POC). `false` expects an external Postgres `Secret` named `postgres.externalSecretName` (default `pfactory-db`, key `database-url`). |
| `externalSecrets.enabled` / `.backend` | recommended for prod | One of `vault`, `aws-sm`, `azure-kv`, `gcp-sm`. Renders the matching ExternalSecret; creates the DB / JWT / OIDC / KMS / Anthropic secrets from your store. Requires External Secrets Operator installed cluster-wide. |
| `kms.backend` | yes | `fernet` (POC, local key) or `aws_kms` / `vault_transit` / `azure_kv` / `gcp_kms` for envelope encryption at rest. |
| `oidc.enabled` + `oidc.issuerUrl` / `oidc.clientId` | for SSO | Client secret comes from `oidc.clientSecretRef` (Secret `pfactory-oidc`, key `client-secret`) or via ExternalSecrets. |
| `migrations.autoApply` | prod = `false` | When `false`, an Alembic upgrade Job runs out-of-band before rollout. Set `true` only for POC/dev. |
| `workspaces.enabled` | optional | Creates a PVC at `workspaces.mountPath` and sets `PROJECT_WORKSPACE_ROOT` so portal git-clone workspaces survive pod restarts (#82). |
| `global.customCABundle.secretName` | if behind TLS proxy | Mounts a corporate root CA and points `SSL_CERT_FILE` at it. |

### Secrets to seed (external-Postgres / non-ExternalSecrets mode)

If you are NOT using ExternalSecrets, create the plain Kubernetes Secrets
the chart references:

```bash
# Database URL (key MUST be "database-url")
kubectl -n pfactory create secret generic pfactory-db \
  --from-literal=database-url='postgresql+asyncpg://user:pass@host:5432/pfactory'

# Fernet key for at-rest KMS (kms.backend=fernet)
kubectl -n pfactory create secret generic pfactory-kms \
  --from-literal=fernet-key="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# OIDC client secret (only if oidc.enabled=true)
kubectl -n pfactory create secret generic pfactory-oidc \
  --from-literal=client-secret='<oidc-client-secret>'
```

Optional operator secrets (see comments in `charts/pfactory/values.yaml`):
`claude-remote-credentials` (Remote Control), `pfactory-mcp-credentials`
(MCP cloud-provider creds), `pfactory-metrics` (scrape bearer token).

### Validate before applying

```bash
helm lint charts/pfactory
helm template pfactory charts/pfactory \
  --set image.repository=ghcr.io/dataseeek/pfactory --set image.tag=1.0.0 \
  | kubectl apply --dry-run=client -f -
```

---

## 4. The MCP server

PFactory ships a stdio MCP server so Claude Code (or any MCP client) can
drive the platform. It is a **local subprocess**, not a network service —
nothing in the Helm chart or the app image launches it. It is wired through
the repo-root [`.mcp.json`](../.mcp.json):

- Server name: `pfactory`
- Launch script: [`scripts/start-pfactory-mcp.sh`](../scripts/start-pfactory-mcp.sh)
  (`scripts/start-pfactory-mcp.cmd` on Windows)
- The script execs `python -m mcp_server.pfactory_server` from the
  `apps/backend/.venv` created by `npm run install:backend`.

It talks to a running web-server over HTTP using these env vars (defaults
in `.mcp.json`):

| Env var | Default | Purpose |
| --- | --- | --- |
| `PFACTORY_API_URL` | `http://localhost:3114` | Web-server base URL (matches the app's `APP_PORT=3114`). |
| `PFACTORY_API_TOKEN_FILE` | `~/.pfactory/.token` | API bearer token, auto-generated on first web-server run. |
| `PFACTORY_PROJECT_DIR` | `${CLAUDE_PROJECT_DIR:-.}` | Repo root for project-scoped operations. |

To use it: start the web-server (compose, Helm, or `python -m server.main`),
then open the repo in Claude Code — the project-scoped `.mcp.json` registers
the `pfactory` server automatically. Point `PFACTORY_API_URL` at your
deployed Ingress host (e.g. `https://pfactory.example.com`) to drive a
remote install.

---

## Requirements

- Kubernetes 1.27+ and Helm 3.16+
- (POC) the `cloudnative-pg` chart repo when `postgres.bundled=true`
- (prod) External Secrets Operator when `externalSecrets.enabled=true`
- Docker / Podman to build the app and runner images
