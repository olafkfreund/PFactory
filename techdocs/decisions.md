# Decisions

The architectural and product decisions that shaped PFactory. The canonical,
versioned log lives in `.agent-os/product/decisions.md` (highest override
priority); this page is the Backstage-facing summary.

## DEC-006 — PFactory is the feasibility & governance gate *(the pivot)*

> 2026-06-04 · **Accepted** · Product / Strategy · supersedes DEC-001, DEC-003; reframes DEC-002

PFactory's identity is the **feasibility & governance gate between an AI plan and
execution, grounded in the customer's live cloud** — *not* a test-generation
product. It sits **downstream** of spec-authoring tools (GitHub Spec-Kit, AWS
Kiro, BMAD-METHOD) and **upstream** of any execution agent. The shipped planning
engine (ingest → enrich → feasibility → review gates → honour-doc → approve →
governed emit) **is the product**. The inherited test-generation engine is
**demoted to a feature**.

**Why:** live-infra grounding + IAM-access + pre-code cost is the hardest-to-copy
asset; the EU AI Act high-risk obligations (Aug 2 2026) make an audit-trailed
governance gate timely; every Spec-Kit/Kiro/BMAD user becomes a lead, not a rival.

**Trade-off:** the most contested arena (free upstream tools, IDPs), a
committee-driven buyer, and high validation risk — securing 3–5 design partners
is the top non-feature priority.

## DEC-005 — Credential Broker: vault-backed cloud auth with honest egress

> 2026-05-30 · **Accepted** · Technical

Agents authenticate to GCP/AWS/Azure/K8s via a pluggable secrets layer
(`apps/backend/pfactory_secrets/`): a `SecretsBackend` abstraction + factory + ref
routing, a `CredentialBroker`, and an explicit per-project egress opt-in
(`.pfactory.yml` `egress.enabled`, default **OFF**). v1 is pass-through (resolve →
inject → wipe); workload-identity federation and sandbox injection are
fast-follows. Secrets never live in the repo; cred files are ephemeral (0600) and
wiped per task; egress is opt-in and auditable.

## DEC-004 — Honest v0.x version line

> 2026-05-30 · **Accepted** · Process

The product version line is `0.x`, not the inherited AIFactory `3.0.2`.
`release.yml` auto-cuts `v<version>` on dev→main promotion.

## DEC-003 — Browser-first lane ordering *(now feature-scoped)*

> 2026-05-30 · **Superseded by DEC-006** — applies only to the demoted test-generation feature

When a feature can be exercised through a browser, generate a Browser test;
otherwise API; otherwise Integration; Unit only as a last resort. Mutation is
orthogonal. Deliberately the opposite of the industry default — browser-first
tests exercise real user-visible behaviour and produce reviewable evidence
(screenshots/video/trace).

## DEC-002 — Application security scanning is out of scope

> 2026-05-30 · **Accepted** (reframed by DEC-006) · Product

PFactory does **not** perform application SAST/DAST/Fuzz. It governs *plans* and
performs *cloud-posture* feasibility (cost / IAM / quota). Application security
scanning of code stays delegated to dedicated pipelines.

## DEC-001 — Standalone product, AIFactory as the wedge *(superseded)*

> 2026-05-30 · **Superseded by DEC-006** · Product / Strategy

Originally recorded PFactory as a standalone **autonomous-QA** product with the
AIFactory handover as a warm-start wedge. DEC-006 re-identifies PFactory as the
governance gate and demotes test-generation to a feature. Retained for history.
