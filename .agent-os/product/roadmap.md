# Product Roadmap

> Last Updated: 2026-05-30
> Version: 1.0.0
> Status: Active

Derived from the 2026-05-30 PM review (internal audit + 15-competitor market
study) captured in epic [#33](https://github.com/olafkfreund/PFactory/issues/33).

## Phase 0: Already Shipped (v0.1.0-mvp → v0.2.0)

- [x] 4-agent pipeline end-to-end — Planner → Gen → Executor → Evaluator → Triager
- [x] 5-signal verdict (coverage · stability · mutation · flake-lint · semantic)
- [x] Unit lane: pytest (Python) **and** Jest (TypeScript)
- [x] Browser lane: Playwright (TypeScript) via `AppRuntime`
- [x] API + Integration lanes (v0.2)
- [x] Framework descriptor registry + `.pfactory.yml` + tests-catalog
- [x] Test evidence capture (screenshots / video / trace / HAR) + portal Evidence tab
- [x] MCP server + `/handover-to-pfactory`, `/pfactory-init`, `/pfactory-add-test`
- [x] Dry-run-by-default git commit + PR comment
- [x] BYO-LLM provider factory (Claude / Codex / Gemini / Ollama / OpenAI-compatible)

## Horizon 1 — Now: credibility blockers

- [x] Reconcile docs ↔ code lane vocabulary (#34 — README/CLAUDE done; docs/ snapshots pending)
- [x] Fix the version stamp 3.0.2 → 0.2.1 (#35)
- [x] Green the CI baseline (#46)
- [ ] This product documentation set (#36)

## Horizon 2 — Next: table-stakes gaps

- [ ] Flaky-test history — cross-run flip-rate signal (#37) `M`
- [ ] BYO-LLM / air-gapped as a marketed, verified feature (#38) `S`

## Horizon 3 — Later: moat & strategy

- [x] Decouple from AIFactory — ingest generic AC sources (Gherkin/EARS/markdown) (#40) `L`
- [ ] Mutation-driven generation across languages (Stryker/PIT) (#41) `L`
- [x] Define freemium GTM + pricing (#42) `M` — see `pricing.md`
- [~] Trim inherited AIFactory dead weight (#43) `L` — tranche 1 done; runners/github cut pending a dedicated refactor

## Effort Scale

- `XS`: 1 day · `S`: 2-3 days · `M`: 1 week · `L`: 2 weeks · `XL`: 3+ weeks
