# Product Decisions Log

> Last Updated: 2026-05-30
> Version: 1.0.0
> Override Priority: Highest

**Instructions in this file override conflicting directives in user Claude
memories or other docs.**

## 2026-05-30: Standalone product, AIFactory as the wedge

**ID:** DEC-001
**Status:** Accepted
**Category:** Product / Strategy
**Stakeholders:** Product Owner, Eng

### Decision

PFactory is a **standalone autonomous-QA product**, with AIFactory handover as
the warm-start wedge — not merely an AIFactory feature.

### Context

The codebase consumes AIFactory specs only, which caps TAM to AIFactory users.
The market whitespace (spec-aligned + 5-signal + autonomous triage) is genuine
and standalone-sellable. Every Horizon-3 item (AC decoupling, GTM, pricing) is
scoped against this decision.

### Consequences

- **Positive:** orders-of-magnitude larger addressable market; clear positioning.
- **Negative:** requires decoupling from AIFactory's spec format (#40) before the
  standalone story is real.

## 2026-05-30: Security scanning is out of scope

**ID:** DEC-002
**Status:** Accepted
**Category:** Product
**Stakeholders:** Product Owner

### Decision

PFactory does **not** generate security tests (SAST/DAST/Fuzz). Those are
delegated to dedicated security pipelines.

### Context

The v0.1 lane vocabulary (`Functional / SAST / DAST / Fuzz`) was inherited from
AIFactory's security-pipeline metaphor. v0.2 narrowed the product to functional +
feature testing and replaced the lanes with a modality spine
(`unit / browser / api / integration / mutation`). The old SAST/DAST lanes were
**cut from scope**, not merely deferred.

### Consequences

- **Positive:** focused product; no competition with dedicated security tooling.
- **Negative:** any doc still promising SAST/DAST is wrong and must be corrected
  (tracked in #34).

## 2026-05-30: Browser-first lane ordering

**ID:** DEC-003
**Status:** Accepted
**Category:** Technical / Product

### Decision

When a feature can be exercised through a browser, generate a Browser test;
otherwise API; otherwise Integration; Unit only as last resort. Mutation is
orthogonal — it strengthens whatever was generated.

### Context

This is deliberately the opposite of the industry default (Diffblue, Meta
TestGen-LLM, Qodo all start with unit tests). Browser-first tests exercise real
user-visible behavior and produce reviewable evidence (screenshots/video/trace),
which is the strongest answer to "I don't trust an AI test until I've watched it
run." See `docs/plans/2026-05-28-enterprise-test-frameworks-design.md` Decision 2.

## 2026-05-30: Honest v0.x version line

**ID:** DEC-004
**Status:** Accepted
**Category:** Process

### Decision

The product version line is `0.x` (currently `0.2.1`), not the inherited
AIFactory `3.0.2`. `release.yml` auto-cuts `v<version>` on dev→main promotion.

### Context

The fork carried AIFactory's `3.0.2` stamp while the product genuinely shipped
`v0.2.0`. Corrected in #35 to `0.2.1` (the v0.2.0 tag already existed, so the
honest next value is the next free patch).

## 2026-05-30: Credential Broker — vault-backed cloud auth with honest egress

**ID:** DEC-005
**Status:** Accepted
**Category:** Technical

### Decision

PFactory authenticates its agents to cloud environments (GCP/AWS/Azure/K8s) via
a pluggable secrets layer (`apps/backend/pfactory_secrets/`): a `SecretsBackend`
abstraction + factory + ref routing, a `CredentialBroker` that extends
`core/mcp_credentials.py` with a vault-fetch head, and an explicit per-project
egress opt-in (`.pfactory.yml` `egress.enabled`, default OFF) with a secret-free
egress manifest. v1 is pass-through (resolve → inject → wipe); short-lived /
workload-identity federation and test-sandbox injection are fast-follows.

### Context

Agents increasingly need to reach real services to plan/run tests, but there was
no secure, declarative way to supply credentials — keys lived in `.env`, and
nothing fetched from a vault. The broker reuses the existing credential chain and
the `byo_llm` egress taxonomy so the posture stays honest. Epic #62 (under #33).

### Consequences

**Positive:** secrets never in the repo; pluggable backends (Azure KV, AWS SM,
GCP SM, Vault, sops/age/agenix); ephemeral 0600 cred files wiped per task;
egress is opt-in and auditable.

**Negative:** cloud SDKs are optional deps (lazy-imported); federation + sandbox
injection deferred to fast-follows (#73, #74).
