# Cloud Infrastructure Testing (AWS / Azure / GCP)

> Status: AWS, GCP and Azure implemented end-to-end (discovery + Prowler
> assessment + diagram + portal view), all live-verified against real accounts.
> Epic [#133](https://github.com/olafkfreund/PFactory/issues/133).

PFactory can assess a cloud account the way it assesses code: **discover** what's
there, **detect misconfigurations**, **diagram** the topology, and emit a
**verdict** (reject / flag / accept) with a report attached to the task.

## The flow

```
Access  ──►  Discover  ──►  Diagram + review  ──►  Assess (Prowler/OCSF)
do we     what's here?    is this what we        which configs are faulty?
get in?   (inventory)     expect? (topology)      → verdict + report
```

Every stage is **read-only** — PFactory never mutates the account.

## Declare a cloud target in `.pfactory.yml`

```yaml
version: 1
egress:
  enabled: true            # required — cloud APIs need network
  destinations:
    - host: "*.amazonaws.com"
targets:
  - name: aws-prod
    type: cloud_provider
    provider: aws          # aws | azure | gcp
    regions: [us-east-1, eu-west-2]
    profile: Calitii       # an ambient CLI profile, OR:
    assume_role: arn:aws:iam::123456789012:role/pfactory-readonly   # read-only role
    scan:
      misconfiguration: true
      compliance: [cis]
      fail_on_severity: high   # the verdict gate (critical|high|medium|low)
```

A `cloud_provider` target **requires `egress.enabled`** (fail-closed). Auth is
either an ambient `profile`, a read-only `assume_role`, or a vault-backed
`auth: { type: ref }` referencing a `test_credentials` entry — never inline
secrets.

## What runs

- **Discovery** (`agents/cloud/discovery.py`) — `access_check` + `discover` →
  a normalized inventory (global S3/IAM + per-region VPC/EC2/Lambda).
- **Assessment** — the **`pfactory-runner-cloud`** image bundles
  [Prowler](https://github.com/prowler-cloud/prowler) (CSPM, 500+ CIS checks,
  OCSF output) + Checkov (IaC). It runs read-only with credentials mounted
  read-only and `--network=bridge`.
- **OCSF → verdict** (`agents/cloud/assessment.py`) — `parse_ocsf` + `assess`
  apply the `fail_on_severity` gate; `to_inventory_findings` feeds the diagram.
- **Diagram** (`agents/diagrams/mermaid.py`) — `render_cloud_topology` emits a
  Mermaid service topology (`graph LR`), failing nodes flagged red.
- **Task-write** (`agents/cloud/report.py`) — `assess_and_write` drops
  `findings/cloud_assessment.{md,json}` + `findings/diagrams/cloud_topology.mmd`.

## Verdict mapping

| Prowler severity (CIS) | PFactory verdict |
|---|---|
| Critical / High FAIL (≥ `fail_on_severity`) | **reject** |
| FAIL below the gate | **flag** |
| No FAILs | **accept** |

Examples of high-signal fails: public S3/GCS buckets, `0.0.0.0/0` ingress to
admin ports, unencrypted EBS/RDS, wildcard IAM, users without MFA, public DBs.

## Viewing the result

The portal **Cloud** view (left sidebar) renders the latest assessment —
verdict badge, the Markdown report, and the Mermaid topology — served by
`GET /api/cloud/assessment` from `~/.pfactory/cloud-assessments/latest`
(override `PFACTORY_CLOUD_ASSESSMENT_DIR`). The same artifacts also land in a
task's `findings/`.

## Security model

- **Read-only only** — `describe`/`list`/`get` + Prowler; never a mutation.
- Use a **read-only** credential (an STS read-only role / viewer roles).
- Credentials are **never printed, logged, or committed**; the Redactor scrubs
  sinks. Cred files mount **read-only** and are wiped after the run.
- Cloud calls are **outward-facing against a real account** — gated behind
  `egress.enabled` + declared destinations, and confirmed before running.

## Run it

Use the **`/cloud-discover`** skill (`.claude/skills/cloud-discover/SKILL.md`) —
it drives access → discover → diagram → assess and writes the report. Or call
the primitives directly (see the skill for snippets).

## Per-provider auth

All three providers share the inventory shape, diagram, report, verdict and
portal view (inventory-driven). They differ only in **how credentials reach
the scan**:

| Provider | Discovery (host CLI) | Prowler auth (in container) |
|----------|----------------------|------------------------------|
| AWS   | `aws` + `--profile`         | `~/.aws` mounted ro, `AWS_PROFILE` |
| GCP   | `gcloud` (project from config or `profile`) | `~/.config/gcloud` mounted ro as `/gcloud`, ADC (`GOOGLE_APPLICATION_CREDENTIALS` + `CLOUDSDK_CONFIG`), run as host uid |
| Azure | `az` (subscription from login or `profile`) | `~/.azure` mounted ro as `/azure` (`AZURE_CONFIG_DIR`), `prowler azure --az-cli-auth`, run as host uid |

GCP/Azure mount 0600 credential files, so the container runs as the host uid
(`--user`) to read them; the runner image bundles `az` for `--az-cli-auth`.
Discovery itself always runs host-side (the host has `aws`/`gcloud`/`az`).
