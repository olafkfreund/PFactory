---
layout: post
title: "How do you let an AI touch your cloud without handing it the keys?"
subtitle: "Real integration tests need real credentials. Real credentials must never touch the repo. The Credential Broker squares that circle."
date: 2026-05-31 12:00:00
author: DataSeek Team
---

There's a tension at the heart of testing live services. To check that your
api-lane test can actually *reach* the thing, it needs real credentials — a
kubeconfig, a cloud token, a database URL. But the one place those secrets must
**never** end up is the repo an AI agent is reading and writing.

The lazy answers are all bad: bake secrets into a `.env` and pray, or skip the
integration tests entirely and ship on vibes. We wanted a third option.

## The broker

PFactory's **Credential Broker** resolves secrets from where they actually
live — HashiCorp Vault, AWS/GCP/Azure secret managers, or local encrypted files
(sops/age/agenix for the air-gapped crowd) — and materialises them *ephemerally*
at the moment a task needs them:

```
ref in config            →  broker  →  env var + 0600 file  →  wiped on close
vault:secret/db#url                     DATABASE_URL=…         (gone after the run)
```

The config never contains a secret — only a **reference** (`vault:…`,
`gcp-sm://…`, `aws-sm://…`). The real value is fetched, dropped into a per-task
scratch dir at `0600`, used, and erased when the task ends. Nothing lands in the
repo, the logs, or your git history.

## Honest egress, off by default

Here's the part we're quietly proud of: **it does nothing unless you opt in.**
With no `egress.enabled`, the broker resolves *zero* cloud credentials. Turn it
on, and it generates a manifest naming every secret → destination, each tagged
with an honest badge — 🔒 local, 🏠 self-hosted, ☁️ managed cloud — so you can
see, at a glance, exactly what would leave your network before it does. Resolved
values are scrubbed from logs. No surprises, no "wait, it sent *what* *where?*"

## Short-lived by design

For the security-minded, the broker also mints **workload-identity** credentials
— AWS STS `AssumeRoleWithWebIdentity` and friends — so a run gets a scoped,
expiring token instead of a long-lived key. It caches them with their TTL and
re-mints as they age out. The blast radius of a leaked demo token: minutes.

## The point

An autonomous agent testing real infrastructure is genuinely useful and
genuinely scary. The trick isn't to trust the agent more — it's to give it
*ephemeral, scoped, opt-in, auditable* access and wipe up after it. That's the
whole design.

Full ref syntax and the backend list are on the
[credentials page](/credentials/); the egress badge shows up wherever you wire a
provider in the portal.
