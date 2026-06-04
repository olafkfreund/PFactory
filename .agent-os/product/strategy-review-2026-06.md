# PFactory — Critical Product & Market Review

> Internal strategy document — **not** published to the public site (lives in
> `.agent-os/product/`, which GitHub Pages does not serve). Created 2026-06-04.
> A deliberately critical assessment researched against the 2026 landscape.

## TL;DR verdict

**The product is real and the instinct is right — but the framing, the focus, and
(above all) the choice of *which* product to be are unresolved.**

- The planning/governance engine is **genuinely shipped** (live cloud pricing, IAM
  policy-simulation, review gates + human approval, governed emit) — not a demo over
  vapor. Build risk is low; **go-to-market and validation risk is high** (zero design
  partners, fictional pricing).
- The *narrow* wedge — "an audit-trailed governance + **live-infra feasibility** gate
  between a spec and the execution agent" — is real, defensible, and timely (EU AI Act
  high-risk obligations land **Aug 2, 2026**, penalties to 7% of turnover).
- The *stated* framing — "we're the only **planning** layer before coding agents" — is
  **wrong** and a credibility liability: Spec-Kit/Kiro/BMAD own spec-authoring, free.
  But they author from the *prompt and stop at text*; none does the feasibility gate.
  So you're not "a worse paid Spec-Kit" — you're a different thing wearing its label.
- The **real unanswered question isn't "fix PFactory's framing" — it's "across the
  three factories, which single product is the sharpest wedge?"** And the evidence
  partly points the *opposite* way from "governance gate" (see §5). That is the
  decision this review exists to force.

## 1. The thesis is contradicted by the market

`docs/market-positioning.md` says: *"no one sells a dedicated, standalone
planning-and-governance incubator that runs before the coding agents."* That is
**false as of 2026**:

- **GitHub Spec-Kit** — open-source (MIT), ~**93k★**, a CLI that drives **Specify →
  Plan → Tasks** across **30+ agents** (Claude Code, Copilot, Cursor, Gemini…). Free,
  agent-agnostic, the de-facto standard.
- **AWS Kiro** — agentic IDE, **Requirements → Design → Tasks**, EARS, AWS-integrated.
- **BMAD-METHOD** — open-source agentic *planning* framework with Analyst / PM /
  Architect / PO / QA roles that **forces PRD + architecture validation before any
  code**, for $0. It overlaps ~80% of PFactory's *authoring/decomposition* — but
  **~0% of the feasibility gate** (no live-infra grounding, no IAM sim, no cost API,
  no policy-gate emit). That gap is precisely your defensible ground.
- And the kicker: **PFactory is itself built on `.agent-os`** (Builder Methods' Agent
  OS), *another* spec/planning framework. You are using a planning framework while
  claiming to be the only one.

**Implication:** "planning" is commoditised to $0 and owned by GitHub/AWS/OSS. Lead
with "planning" and you're a worse, paid Spec-Kit. Your real wedge is **everything
those tools DON'T do**: ground the plan in the *running system* and gate it.

## 2. Where you genuinely differentiate vs. reinvent

| Capability | Verdict | Reality |
|---|---|---|
| **Live-infra grounding** (read-only AWS/Azure/GCP/k8s introspection feeding the plan) | ✅ **genuine, defensible** | Spec-Kit/Kiro/BMAD plan from the *prompt*; none read your running cloud. Your strongest, hardest-to-copy asset. |
| **Technical-access feasibility** (IAM policy-simulation: "can the principal even do this?") | ✅ **novel, but thin** | Nobody does pre-plan IAM simulation. Real, but a feature, not a moat. |
| **Cost feasibility** | 🟡 **overlaps Infracost — different stage** | Infracost is the FinOps standard, but it prices **declared IaC (HCL/Terraform)** — resources that don't exist yet at *plan* time. PFactory's `cost.py` prices a coarse shape from **plan text + live cloud APIs** *before any code*, a stage Infracost can't reach. **Keep the upstream pre-code estimator; integrate Infracost downstream once IaC exists.** Watch: Infracost is moving toward AI agents. |
| **Policy gates / Backstage templates** | 🟡 **overlaps IDPs + policy-as-code** | Port/Cortex/Backstage own golden-paths + scorecards; OPA/Conftest/Checkov own policy-as-code. Your value is *composing* these for AI plans, not replacing them. |
| **Governed emit + audit trail + human approval** | ✅ **genuine, timely** | The compliance story the execution vendors lack — and the EU AI Act makes it a tailwind. Keep it. |
| **MCP-first / agent-agnostic handoff** | ✅ **right call** | Execution-agnostic emit (GitHub issues any agent can take) is correct and on-trend. |

**Net:** 2 strong assets (live-infra grounding, governed/auditable emit), 1 novel-thin
(IAM access), 1 reinvention to integrate-not-rebuild (cost → Infracost downstream),
2 "integrate not compete" (policy, IDP templates).

## 3. The competition is moving the other way

- **Cognition/Devin** ($25B raise talks) went **enterprise via SIs** — Cognizant
  (Jan 28 2026) and Infosys — selling "enterprise-grade delivery, **governance** and
  scale." Governance is being bundled into *execution + SI channel*, not sold as a
  separate planning product.
- The 2026 guardrails discourse describes **multi-agent validation chains** ("one
  agent writes, another critiques, a third tests, a fourth validates compliance/
  architecture") — i.e. your governance gate is being absorbed as *a step inside the
  execution loop*, the exact risk your own doc flags.
- IDP reality is sobering: **~70% of platform initiatives fail on adoption**; teams
  spend 60% of their time maintaining Backstage. A standalone "governance portal"
  fights that same adoption gravity.

## 4. The internal problems are worse than the external ones

1. **A pivot caught mid-stride in your own source-of-truth.** `.agent-os/product/`
   (mission · roadmap · **pricing**, all 2026-05-30; `decisions.md` marked "Override
   Priority: Highest") describe a **test-generation product** (fork residue from
   TFactory). `docs/market-positioning.md` (2026-06-03) + the shipped `plan/` engine
   describe a **planning/governance product**. Both are **real and shipped** — a
   pivot-in-progress, not incoherence-by-neglect, but the canonical docs (and pricing)
   still sell the *old* product. The org hasn't *decided* which product it is (§5).
2. **Three products, one (apparently tiny) team.** AIFactory (execute) · TFactory
   (test) · PFactory (plan), each vs. a funded incumbent (Devin/$25B, Copilot,
   Spec-Kit/93k★, Infracost, Port/Cortex). "Suite leverage" is a liability disguised
   as a strength: 3× surface area, 3× maintenance, **none best-in-class.** Single
   biggest existential risk.
3. **No design partners, no validation.** The *product* is built — but `pricing.md`
   admits prices are "illustrative," billing is unbuilt, "needs design-partner
   validation." Market docs cite blog posts, **not one customer interview.** Build
   risk low; "will anyone *pay*" risk wide open. Don't conflate the two.
4. **Differentiators are integration-heavy and individually shallow** — each slice is
   absorbable by one adjacent incumbent. The *combination* is the only moat, and
   combinations are fragile.

## 5. The real decision: which product is PFactory? (two honest options)

You have **two real, shipped wedges** in one repo. Pretending they're one product is
the mistake. Pick one as the spearhead.

### Option A — The feasibility & governance gate (the planning bet)
*"The compliance-grade gate between an AI plan and execution, grounded in your live
cloud."* Sit **downstream** of Spec-Kit/Kiro/BMAD (consume their spec — every Spec-Kit
user becomes a lead) and **upstream** of any execution agent. Lead with the two assets
nobody else combines: (a) live-cloud cost + **IAM-access** + quota checks *before
code*, (b) audit trail + human approval (EU-AI-Act-ready). **Integrate** Infracost,
OPA/Checkov, Port/Cortex — orchestrate, don't re-implement.
- **Pro:** timely (EU AI Act), defensible (live-infra hard to copy), agent-agnostic.
- **Con:** the *most contested* arena — Spec-Kit (free), AWS/GitHub upstream, IDPs,
  ~**70% platform-initiative adoption failure.** Committee-driven buyer, slower motion.

### Option B — Double down on autonomous QA (the test bet)
The thing your *code and pricing already are*: spec-aligned test generation + the
5-signal verdict, validated against **Qodo / Diffblue / Cover-Agent** (who generate
from *code*, not *intent*). **Weaker, less-crowded field than planning**, the per-repo
pricing already fits, more-shipped/more-validated. Fold the `plan/` engine into
AIFactory as a feature, not a third product.
- **Pro:** sharper, less-contested wedge; pricing/GTM already designed; developer-led
  bottoms-up motion (faster than committee sales).
- **Con:** "AI test gen" is a busy mid-market; walks away from the timelier governance
  narrative and the live-infra moat just built.

### My read (decisive, as asked)
**Small team → Option B is the safer, sharper bet** (more built, weaker competition,
developer buyer, pricing exists). **A regulated design partner in 60 days → Option A is
the bigger prize** (live-infra + EU-AI-Act gate, a fundable wedge nobody owns). What you
cannot afford is **Option C — keep all three factories and ship none best-in-class.**
That is the current trajectory and the most likely way this dies.

## 6. What to do next (priority order)

1. **Make the Option A vs B decision (§5).** Everything below forks on it. A founder
   call, not a feature call — make it deliberately, this week.
2. **Resolve the doc/identity lag** (~free): rewrite/retire `.agent-os/product/` so
   mission · roadmap · **pricing** describe whichever product you chose. Today they
   sell the *old* one. *Critical blocker for either path.*
3. **Get 3–5 design partners before any more features.** The demo opens doors. For A:
   will a platform/compliance team *pay* for a governance gate? For B: will a team
   *trust and pay for* spec-aligned tests? `pricing.md` is fiction until tested.
   **This single step de-risks the strategy more than any code.**
4. **Kill the "empty market" claim** in `market-positioning.md` regardless of path —
   false and a credibility liability. Name Spec-Kit/Kiro/Infracost honestly.
5. **If Option A:** keep the pre-code estimator, *integrate* Infracost/OPA/Checkov/Port
   downstream, lead with IAM-access + audit trail; beachhead = regulated orgs adopting
   coding agents. **If Option B:** fold the `plan/` engine into AIFactory; double down
   on the 5-signal verdict vs Qodo/Diffblue; beachhead = Python/TS teams burned by
   low-value AI tests.
6. **Either way, stop running three full products on one team.** Demote two to features
   or pause them. Focus is the moat you can actually afford.

## 7. Are you on the right track?

**Instinct: yes. Focus: no — and that's the whole game.** The bet that *velocity
without verification* is 2026's real bottleneck is correct and well-evidenced, and you
have genuinely shipped, differentiated tech on both the QA and the governance side. The
problem is you've built **two fundable products and committed to neither**, wrapped the
better-positioned one in a false "only planning layer" claim, and stretched a small team
across three factories. The work is good; the *strategy* is undecided.

You are not on the wrong track — you're at a fork and standing still. Pick **A or B**
(§5), make the docs and pricing say it, put the demo in front of 5 real buyers, and
demote the other two factories to features. Do that and there's a real company here.
Stay diffuse across three products and the most likely outcome isn't failure of the
tech — it's an execution vendor, an IDP, or Infracost shipping your best feature first
while you maintain three things at once.

## Sources
- Spec-Kit / Kiro / SDD: marktechpost.com (9 best SDD tools 2026); medium (Kiro vs Spec Kit); augmentcode.com
- BMAD-METHOD: github.com/bmad-code-org/BMAD-METHOD; docs.bmad-method.org
- Devin/Cognition enterprise+SI: news.cognizant.com (2026-01-28); cognition.ai/blog/infosys-cognition
- Guardrails/governance 2026 + EU AI Act: technologyreview.com; checkmarx.com; atlan.com
- Infracost: github.com/infracost/infracost; infracost.io
- IDP/Backstage/Port/Cortex: tasrieit.com (Port vs Backstage vs Cortex 2026); platformengineering.com
- Cost-underestimation stat: softermii.com / riseuplabs (AI agent cost 2026)
