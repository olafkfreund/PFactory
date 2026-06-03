# GTM & Pricing Model

> Last Updated: 2026-05-30
> Version: 1.0.0
> Status: Proposed (decision artifact — no billing is implemented)

This is a written, reviewable pricing + go-to-market model for PFactory
(issue #42, epic #33). It is a **decision document**, not an implementation
spec — billing/metering code is a separate future epic. It is grounded in
the 2026-05-30 PM market study (Qodo, Keploy, CodeRabbit, Tusk, Diffblue,
Mabl) and the product decisions in `decisions.md`.

## Positioning recap

> "Autonomous test generation aligned to acceptance criteria — validated by
> mutation testing and semantic relevance, not just coverage."

PFactory is an **async agent**, not an IDE seat. That single fact drives the
metering choice below.

## Metering unit — per-repo, not per-seat

| Unit | Verdict | Why |
|---|---|---|
| **Per-repo / per-PR** | ✅ **chosen** | PFactory runs in CI per PR, async, with no human at a keyboard. Value scales with repos under test, not headcount. Matches CodeRabbit / Keploy. |
| Per-seat | ❌ | Wrong for an agent nobody "sits at" — teams game it by sharing one seat; punishes the PR-native workflow. |
| Pure usage (per-token) | ⚠️ secondary | Opaque to buyers and unbounded; fine as an enterprise overage lever, bad as the primary unit. |

**Primary meter:** an **active repo** (a repo that received ≥1 PFactory run in
the billing month). **Secondary meter (free tier only):** **test-generation
runs per repo per month** (a "run" = one Planner→Triager pipeline pass).

> A BYO-LLM run (issue #38, 🔒 Local) costs us no inference — so pricing
> meters the *orchestration*, not tokens. This is a deliberate differentiator:
> regulated teams on local models pay for the platform, not per-call.

## Tiers

| Tier | Price (starting point — **illustrative, to be validated**) | Meter | For |
|---|---|---|---|
| **Free / OSS** | $0 | 1 repo · 50 runs/mo · PR comment | individuals, OSS, evaluation |
| **Team** | ~$40 / active repo / mo | unlimited runs · all 5 signals · flaky-history · evidence capture | startups & mid-market |
| **Enterprise** | custom (annual) | on-prem / BYO-LLM / air-gapped · RBAC / SSO · audit · priority support | regulated & large orgs |

Notes:
- Dollar figures are **starting anchors for validation**, not committed prices
  (Mabl mid-market sits ~$3–6k/mo; CodeRabbit ~$24–48/seat; per-repo at ~$40
  lands a 10-repo team at ~$400/mo — competitive and value-aligned).
- The **Free tier must be genuinely useful** (the OSS-adoption flywheel that
  worked for Qodo / Keploy / CodeRabbit), not a crippled demo.
- **Enterprise's anchor is BYO-LLM / air-gapped (#38)** — the one thing the
  managed-cloud competitors can't easily match.

## What gates each tier (maps to shipped features)

- **Free:** unit lane (pytest), 1 repo, capped runs, PR comment.
- **Team:** all lanes (browser/api/integration/mutation), the full 5-signal
  verdict + flaky-history (#37), evidence capture, generic AC sources (#40).
- **Enterprise:** local/air-gapped LLM with verified no-egress (#38), SSO/RBAC
  (inherited OIDC surface), audit chain, dedicated support, custom frameworks.

## Go-to-market motion

**Freemium → PR-native → land-and-expand** (the pattern that wins in this
category — every successful competitor uses it):

1. **Land (warm):** AIFactory users via `/handover-to-pfactory` — zero-friction,
   tests appear on their PR. The original wedge (DEC-001).
2. **Land (cold):** any team with acceptance criteria — `pfactory-init` +
   generic AC ingestion (#40: markdown / Gherkin / EARS). OSS-first free tier
   drives self-serve adoption.
3. **Expand inside the org:** Free repo → Team (more repos, advanced signals)
   → Enterprise (privacy/compliance). The buyer journey rides on *trust*: the
   5-signal verdict + evidence + flaky-history are the upsell proof points.
4. **Sell on trust, not coverage:** the differentiator messaging is "tests you
   can trust" (mutation + semantic + flip-rate), aimed at teams burned by
   low-value AI tests.

### Target segments (in priority order)
1. AIFactory teams (warm intro).
2. Python/TS-heavy startups & mid-market that distrust low-value AI tests.
3. Regulated industries (finance/health/defence) — led with BYO-LLM/air-gapped.

## Out of scope (explicitly)

- Billing/metering implementation, payment integration, usage-tracking
  infrastructure — a separate future epic once this model is validated.
- Final price points — these need design-partner validation interviews.

## Open questions for validation

- Is "active repo" the right primary meter, or per-PR-run for very large
  monorepos? (A monorepo = 1 repo but 1000s of PRs.)
- Where does the Free→Team line sit (run cap vs repo cap vs lane gating)?
- Does Enterprise need a per-run floor for BYO-LLM (no inference cost to us)?
