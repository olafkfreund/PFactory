---
name: cloud-discover
description: Run a read-only cloud infrastructure assessment — discover an AWS/Azure/GCP account's resources, detect misconfigurations with Prowler (CIS/OCSF), build a service-topology diagram, and emit a verdict (reject/flag/accept). Follows the access → discover → diagram → assess flow and writes the report into findings/. Use for cloud security posture, misconfiguration audits, or "what's in this account".
when_to_use: When the user wants to assess a cloud account/subscription/project — AWS, Azure, or GCP. Triggers — "/cloud-discover", "audit my AWS account", "find misconfigurations in AWS/Azure/GCP", "cloud security posture", "IAM/RBAC audit", "what resources are in this account", "run Prowler", "CIS benchmark scan", "cloud discovery".
allowed-tools:
  - Bash
  - Read
  - Write
---

# /cloud-discover

Run a **read-only** cloud assessment and produce a report + diagram a follow-up
task can act on. The operating model is the natural cloud flow:

> **Access** (do we get in?) → **Discover** (what's here?) → **Diagram + review**
> (is this what we expect?) → **Assess** (which configs are faulty?).

**Read-only, always.** Only `describe`/`list`/`get` calls and Prowler (which is
itself read-only) are run. Never create, modify, or delete a cloud resource.
Credentials are sensitive: never print, log, or commit their values; a live run
is outward-facing against the user's real account — confirm scope before any call.

## Inputs

- **provider** (`aws` | `azure` | `gcp`) — all three implemented
  (`agents/cloud/discovery._IMPLEMENTED`). Discovery shells out to the host CLI
  (`aws` / `gcloud` / `az`); the in-container Prowler scan handles per-provider
  auth (AWS profile · GCP ADC · Azure `--az-cli-auth`).
- **profile / account** — AWS: a named profile (`--profile Calitii`). GCP: the
  `profile` field pins the project id (else ADC's default project). Azure: the
  `profile` field selects the subscription (else the active `az` login). Ask
  which account if unclear; confirm before scanning.
- **regions** — default to the profile's region; ask if multi-region matters.
  GCP/Azure enumerate globally (no per-region fan-out in discovery).
- **services / scope** (optional) — restrict to e.g. `iam`, `s3`, `ec2` (AWS);
  `storage`, `iam` (GCP); `storage`, `resource_groups`, `compute` (Azure).
- **fail_on_severity** (default `high`) — the verdict gate.

## Workflow

1. **Access check** — confirm credentials work and report identity:
   ```bash
   aws sts get-caller-identity --profile <profile> --output json
   ```
   Or use `agents.cloud.discovery.access_check(provider, profile=...)`.

2. **Discover** — read-only inventory into the normalized dict:
   ```python
   from agents.cloud.discovery import discover
   inv = discover("aws", profile="<profile>", regions=[...])
   ```

3. **Assess** — run Prowler in the cloud runner image (`pfactory-runner-cloud`),
   read-only, creds mounted read-only, network on:
   ```bash
   docker run --rm --network=bridge \
     -v ~/.aws:/home/pfactory/.aws:ro -v <scratch>:/scratch:rw \
     -e AWS_PROFILE=<profile> pfactory-runner-cloud:latest \
     prowler aws --service iam --service s3 --service ec2 \
       --output-formats json-ocsf --output-directory /scratch
   ```

4. **Verdict + artifacts** — map Prowler's OCSF to a verdict and write the
   report + diagram into the task's `findings/`:
   ```python
   from agents.cloud.report import assess_and_write
   assess_and_write(spec_dir, inventory=inv, ocsf=open(ocsf_path).read(),
                    fail_on_severity="high")
   # → findings/cloud_assessment.{md,json} + findings/diagrams/cloud_topology.mmd
   ```
   The portal renders these under the **Cloud** view (`/api/cloud/assessment`).

## What to flag (severity → verdict)

Driven by Prowler's OCSF severity (CIS-mapped):

- **Critical / High → `reject`** — public S3/GCS, `0.0.0.0/0` admin ingress,
  unencrypted data stores, wildcard IAM (`*:*`), users without MFA, public DBs,
  disabled logging on root.
- **Medium → `flag`** — over-broad roles, missing encryption defaults, stale keys.
- **Low / Info → `accept` + note.**

`assess(findings, fail_on_severity=...)` applies the gate: any FAIL at/above the
gate → reject; FAILs below → flag; none → accept.

## Output

- `findings/cloud_assessment.md` — verdict, inventory, top failing checks.
- `findings/cloud_assessment.json` — structured result (portal/API).
- `findings/diagrams/cloud_topology.mmd` — Mermaid service topology (red = findings).

See `guides/cloud-testing.md` for the `.pfactory.yml` `cloud_provider` target,
the security model, and how to add the next provider.
