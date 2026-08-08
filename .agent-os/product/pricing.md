# GTM & Pricing Model

> Last Updated: 2026-06-04
> Version: 2.0.0
> Status: Proposed (decision artifact — no billing is implemented; **unvalidated**)

A written, reviewable pricing + go-to-market model for PFactory as the
**feasibility & governance gate** (DEC-006). It is a **decision document**, not an
implementation spec — billing/metering code is a separate future epic. **Every
dollar figure here is illustrative and needs design-partner validation; we have
zero design partners today** (see `roadmap.md` Horizon 1, `strategy-review-2026-06.md`
§4). Do not treat any number below as committed.

## Positioning recap

> "The compliance-grade gate between an AI plan and execution, grounded in your
> live cloud — feasibility (cost · time · access) + governed, audit-trailed emit."

PFactory is an **async governance service**, not an IDE seat. Value accrues to the
*org adopting coding agents*, and scales with the plans flowing through the gate and
the cloud estates they touch — not with headcount.

## Metering unit — per governed plan + per connected estate

| Unit | Verdict | Why |
|---|---|---|
| **Per governed plan** (a plan taken through feasibility + gates to an approve/emit decision) | ✅ **chosen (primary)** | The unit of value *is* a governed verdict. Aligns price with the thing the buyer is paying to trust. |
| **Per connected cloud estate** (an AWS/Azure/GCP account or k8s cluster wired for live enrichment) | ✅ **chosen (secondary)** | The live-infra grounding is the moat and the cost driver (pricing-API + IAM-sim calls). Connecting an estate is where deep value starts. |
| Per-seat | ❌ | Wrong for a service nobody "sits at"; punishes the async, agent-handoff workflow. |
| Pure usage (per-token) | ⚠️ enterprise overage lever only | Opaque and unbounded; BYO-LLM runs cost us no inference, so tokens are the wrong primary unit. |

> A BYO-LLM run (LOCAL, DEC-005 / #38) costs us no inference — so pricing meters
> the *governance + grounding orchestration*, not tokens. Regulated teams on local
> models pay for the platform and the live-infra checks, not per-call.

## Tiers

| Tier | Price (**illustrative — unvalidated anchor**) | Meter | For |
|---|---|---|---|
| **Free / OSS** | $0 | 1 connected estate · capped governed plans/mo · dry-run emit | individuals, OSS, evaluation |
| **Team** | ~$X / connected estate / mo + plan allotment | unlimited estates within reason · full feasibility (cost·time·access) · all review gates · audit trail | platform & architecture teams |
| **Enterprise** | custom (annual) | on-prem / BYO-LLM / air-gapped · SSO/RBAC · EU-AI-Act audit-pack · priority support | regulated & large orgs |

Notes:
- Figures are **starting anchors for validation interviews**, not committed prices.
  The comparable buyers are platform/IDP and compliance budgets, not per-seat dev
  tools — a different (and less price-sensitive) wallet than the QA-tool market.
- The **Free tier must be genuinely useful** (the adoption flywheel), not a demo.
- **Enterprise's anchor is the audit-pack + BYO-LLM / air-gapped** — the EU-AI-Act
  compliance artifact and no-egress posture the managed-cloud competitors can't match.

## What gates each tier (maps to shipped features)

- **Free:** 1 connected estate, capped governed plans, all gates, dry-run emit only.
- **Team:** unlimited estates, full feasibility (live cost + IAM-sim + quotas),
  honoured-document suggestions, Backstage-template policy enforcement, governed
  (non-dry-run) emit, board.
- **Enterprise:** local/air-gapped LLM with verified no-egress (#38), SSO/RBAC,
  EU-AI-Act audit-pack export, Credential Broker with vault backends (DEC-005),
  dedicated support, custom templates/policy packs.

## Go-to-market motion

**Land via an existing spec tool → grounded feasibility → govern → expand by estate.**

1. **Land (consume the upstream):** every Spec-Kit / Kiro / BMAD user is a lead, not
   a competitor — they have a spec and no way to prove it's buildable. PFactory takes
   their output and grounds it. This is the wedge: *we are the step after the step
   they already do for free.*
2. **Land (warm, suite):** AIFactory / TFactory users — PFactory completes
   plan → build → test, governed end to end.
3. **Hook on feasibility:** the live cost + IAM-access verdict is the demo that opens
   the door (a number and a grant/deny nobody else produces pre-code).
4. **Expand by estate + compliance:** connect more cloud accounts; Enterprise upgrade
   rides the audit-pack and air-gapped posture (EU-AI-Act tailwind).

### Target segments (in priority order)
1. Regulated orgs adopting coding agents (finance/health/public) — led with the
   audit trail + live-infra feasibility (the sharpest, least-contested wedge).
2. Platform / Backstage teams operationalising golden paths for AI plans.
3. Existing AIFactory / TFactory users (warm, suite completion).

## Out of scope (explicitly)

- Billing/metering implementation, payment integration, usage-tracking — a separate
  future epic, *after* this model is validated with design partners.
- Final price points — these need validation interviews; the `~$X` above is a
  deliberate placeholder, not a hidden number.

## Open questions for validation

- Is "per governed plan + per connected estate" the right pair, or does the buyer
  expect a flat platform fee with unlimited plans?
- Where does Free→Team sit — plan cap, estate cap, or governed-emit gating?
- Does Enterprise need a per-estate floor for BYO-LLM (no inference cost to us)?
- Is the buyer's budget line "platform/governance" or "AI tooling"? (changes the
  anchor by ~10×.)
