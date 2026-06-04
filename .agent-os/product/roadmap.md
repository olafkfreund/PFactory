# Product Roadmap

> Last Updated: 2026-06-04
> Version: 2.0.0
> Status: Active

PFactory is the **feasibility & governance gate** in front of the execution agents
(see `mission.md`, DEC-006 in `decisions.md`). This roadmap reflects the shipped
planning engine and the forward bets, scoped against that decision.

## Phase 0: Already Shipped — the planning engine

The end-to-end planning pipeline is on `main` (backend + portal, fully tested):

- [x] **Ingest / handover** — docx / pdf / markdown upload, MCP control plane, CLI,
      GitHub issue; consumes Spec-Kit / Kiro / BMAD output and generic AC sources
      (markdown / Gherkin / EARS)
- [x] **Enrich** — read-only introspection of running Kubernetes / OpenShift /
      Azure / AWS / GCP + Terraform + internal wikis & Backstage → cited *AI Context*
- [x] **Detect / decompose / synthesize** — category detection, epic + child-issue
      decomposition with dependencies
- [x] **Feasibility gate** — cost (real AWS Price List / Azure Retail / GCP Catalog
      APIs + static fallback), technical access (`iam:SimulatePrincipalPolicy`),
      calibrated effort rollup
- [x] **Review gates** — architecture / security / best-practice / feasibility
      lenses (deterministic policy-as-code + LLM), cited + deduped + actionable
- [x] **Honour-document annotate** — cited, anchored suggestions on the original +
      an improved draft; the source doc is never overwritten
- [x] **Board projection** — Plans ready → In Progress → AI Review → Human Review →
      Done
- [x] **Human approval + governed emit** — tagged GitHub epic + child issues with a
      `pfactory:meta` block; dry-run by default
- [x] **MCP- & API-first handoff** — `mcp__pfactory__plan_*` tools (categories /
      ingest / process / status / get / list / approve)
- [x] **Categories + Backstage-compatible templates** with embedded `policy:` blocks
- [x] **Tag taxonomy contract** published (`docs/tag-taxonomy.md`) + pickup issues
      filed in AIFactory and TFactory
- [x] **BYO-LLM egress posture** (LOCAL / SELF_HOSTED / MANAGED_CLOUD) + Credential
      Broker (vault-backed cloud auth, opt-in egress)

## Horizon 1 — Now: prove demand, then position

The build risk is low; the validation risk is high (see `pricing.md`). These come
before any new feature work.

- [x] Align product docs to the governance-gate identity (#36 — this set)
- [x] Kill the "empty market" claim; honest competitor map (`market-positioning.md`)
- [ ] **Get 3–5 design partners** — will a platform/compliance team *pay* for a
      governance gate? `pricing.md` is illustrative until this is tested. `XL`
- [ ] Validate the metering unit against a real monorepo buyer (#42) `M`

## Horizon 2 — Next: deepen the moat (live-infra + audit)

- [ ] Bidirectional handback loop hardening (epic #182) — failing-build → correction
      → re-plan, bounded cycles `L`
- [ ] EU-AI-Act audit-pack export (epic + issues + gate scores + approval as a
      compliance artifact) `M`
- [ ] Integrate Infracost downstream once IaC exists (don't re-implement IaC cost) `M`
- [ ] Deepen policy-as-code composition (OPA / Conftest / Checkov) behind the gates `L`

## Horizon 3 — Later: focus the suite

- [ ] Decide the factory portfolio — run one product best-in-class, demote the
      others to features (the focus bet; see `strategy-review-2026-06.md` §5) `XL`
- [ ] Port / Cortex / Backstage scorecard integration (orchestrate, don't replace) `L`

## Effort Scale

- `XS`: 1 day · `S`: 2-3 days · `M`: 1 week · `L`: 2 weeks · `XL`: 3+ weeks
