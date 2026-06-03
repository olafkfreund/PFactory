---
layout: default
title: Credentials
permalink: /credentials/
nav_order: 5.7
---

# Credential Broker — authenticate agents to your cloud

<div class="reveal" markdown="1">

PFactory's agents increasingly need to reach **real services and cloud
environments** to plan, generate, and run tests — a staging API, a Kubernetes
cluster, a Google/AWS/Azure project. The Credential Broker (epic
[#62](https://github.com/olafkfreund/PFactory/issues/62)) gives them a secure,
declarative way to obtain those credentials **without ever putting a secret in
the repo**, and with an honest accounting of what leaves your network.

</div>

## The problem it solves

Before the broker, the only way to give an agent a credential was a raw
environment variable or a key checked into config. That is:

- **unsafe** — secrets leak into shells, logs, and git history;
- **inflexible** — no way to pull from the vault your org already runs;
- **opaque** — nothing told you *what* a run would reach over the network.

The broker replaces all three with a pluggable resolution layer + an explicit
egress gate.

## What it does

<ul class="feature-row">
  <li class="feature-row__card reveal">
    <span class="feature-row__icon" aria-hidden="true">🔌</span>
    <h3>Pluggable backends</h3>
    <p>Resolve secrets from a vault — <strong>Azure Key Vault</strong>, <strong>AWS Secrets Manager</strong>, <strong>GCP Secret Manager</strong>, <strong>HashiCorp Vault</strong> — or local encrypted files (<strong>sops / age / agenix</strong>). One ref syntax, lazily-loaded SDKs.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 80ms">
    <span class="feature-row__icon" aria-hidden="true">🎟️</span>
    <h3>Ephemeral by design</h3>
    <p>File credentials (kubeconfig, GCP ADC) are written <strong>0600</strong> to a per-task scratch dir and <strong>wiped</strong> when the task ends. Values are redacted from logs. Never persisted in the clear.</p>
  </li>
  <li class="feature-row__card reveal" style="--reveal-delay: 160ms">
    <span class="feature-row__icon" aria-hidden="true">☁️</span>
    <h3>Honest egress</h3>
    <p>Off by default — <strong>no cloud credential is resolved</strong> unless the project opts in. <code>pfactory_secrets.cli audit</code> prints a secret-free manifest of exactly what would leave your network, with a 🔒 / 🏠 / ☁️ badge.</p>
  </li>
</ul>

## Why we built it this way

| Decision | Why |
|---|---|
| **Agents first** (not the test sandbox) | The immediate need was agents reaching cloud APIs to plan/generate tests. Sandbox-test injection is a deliberate fast-follow ([#73](https://github.com/olafkfreund/PFactory/issues/73)). |
| **Extend, don't replace** | The broker builds on PFactory's existing `core/mcp_credentials.py` chain (K8s / AWS-IRSA / Azure-MI / GCP-ADC) — it adds a "fetch from a vault" head, it doesn't reinvent ambient auth. |
| **Pass-through in v1** | Resolve → inject → wipe. Minting short-lived federated credentials (OIDC → STS / Workload Identity) is a fast-follow ([#74](https://github.com/olafkfreund/PFactory/issues/74)) so v1 ships sooner. |
| **Egress opt-in + manifest** | The same honest posture as PFactory's [BYO-LLM egress badge](https://github.com/olafkfreund/PFactory/blob/main/guides/byo-llm.md): you should always be able to see what a run touches before it runs. |

## Reference syntax

```text
env:STAGING_API_TOKEN                 # an environment variable
sops:secrets.enc.yaml#api_token       # a sops-encrypted file + key
agenix:db-password.age                # an agenix / age file
vault:secret/data/staging#token       # HashiCorp Vault (KV-v1/v2)
azurekv://my-vault/STAGING-TOKEN      # Azure Key Vault
aws-sm://staging/api#token            # AWS Secrets Manager (JSON field)
gcp-sm://my-project/sa-key            # GCP Secret Manager (optional /version)
```

## Try it

```bash
# 1. Declare creds + opt into egress in .pfactory.yml
egress:
  enabled: true
credentials:
  gcp: { ref: "gcp-sm://my-proj/pfactory-sa", as: GOOGLE_APPLICATION_CREDENTIALS, kind: file }

# 2. See exactly what would leave your network (secret-free)
python -m pfactory_secrets.cli audit .

# 3. Check which backends are wired here
python -m pfactory_secrets.cli doctor
```

Full reference: [`guides/credentials.md`](https://github.com/olafkfreund/PFactory/blob/main/guides/credentials.md).
Design: [`docs/plans/2026-05-30-credential-broker-design.md`](https://github.com/olafkfreund/PFactory/blob/main/docs/plans/2026-05-30-credential-broker-design.md).

## What's next

- **[#73](https://github.com/olafkfreund/PFactory/issues/73)** — inject scoped credentials into the test **sandbox**, so generated api/integration tests can authenticate to the service-under-test.
- **[#74](https://github.com/olafkfreund/PFactory/issues/74)** — short-lived / **workload-identity federation** (OIDC → STS / GCP WIF / Azure federated tokens).
