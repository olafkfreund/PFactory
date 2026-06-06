# Planning-Process Hardening — Design Spec + Backlog

> Created: 2026-06-06
> Status: Approved (design) — implementation not yet started
> Scope: `apps/backend/plan/` (planning engine) + `plan/emit/` (AIFactory handover)
> Origin: `/super-brainstorm` review of the planning process

## Context — why this change

PFactory's planning engine is the feasibility & governance gate between an AI plan
and execution. Its biggest strength — *"help, never override," fault-tolerant to a
fault* — is also the biggest obstacle to the goal: making planning **100%
bulletproof** and **verifying all task information is present and working** before
handover to AIFactory.

Two structural weaknesses block that goal:
1. **Completeness is never enforced.** Missing/unverified information (empty ACs,
   unmapped criteria, denied IAM, failed enrichment, silent LLM fallback) degrades
   to advisories and emits anyway. "approved" does not mean "complete."
2. **The handover to AIFactory is thin and unverified.** `requirements.json` drops
   the dependency graph, the live-cloud context, the feasibility/access data, and
   the citations PFactory worked to gather — and the POST is fire-and-forget, with
   no proof AIFactory received an intact task.

Confirmed product decisions driving this design:
- **Hard completeness gate WITH a recorded human waiver override** (not unconditional).
- Handover must **add**: dependency graph, enrichment context, feasibility metadata
  (incl. denied IAM), citations + per-child definition-of-done.
- Handover must become a **validated handshake** (receipt + field-integrity check).

## What we already do well (keep, build on)

- Deterministic-first pipeline; LLM is an optional seam with heuristic fallback.
- Content-hash-bound approval (`NormalizedPlan.canonical_content()` → hash;
  `HumanApproval.valid`) — editing invalidates sign-off. **Reuse this exact pattern
  for waivers.**
- Single clean emit gate: `ready_to_emit() == gates_passed AND approved AND valid`,
  enforced in `emit/github_emitter.py`.
- Machine-readable `<!-- pfactory:meta -->` block + full label taxonomy.
- Multi-lens review + deterministic policy rules; graceful degradation throughout.

## Confirmed gap → fix map

| # | Gap (today) | Fix (workstream) |
|---|-------------|------------------|
| 1 | Missing info silently defaulted, never blocks | WS1 readiness gate |
| 2 | Empty/weak ACs allowed; no testability check | WS1 `criteria-present`, `ac-testable` |
| 3 | No AC→child coverage proof | WS1 `ac-child-coverage` |
| 4 | Denied IAM (`granted=False`) non-blocking | WS1 `access-granted` (hard) |
| 5 | Enrichment failure silently = "no cloud" | WS1 `enrichment-integrity` |
| 6 | Silent LLM fallback / swallowed template checks | WS1 `decompose-trustworthy` + record signal |
| 7 | Dangling deps non-blocking | WS1 `deps-sound` |
| 8 | Handover payload thin (context dropped) | WS2 contract v2 |
| 9 | Fire-and-forget handover | WS3 validated handshake |

---

## Workstream 1 — Readiness/Completeness gate + waiver

**Architecture:** new package `plan/review/readiness/` (mirrors `plan/review/rules/`).
Readiness is a **hard binary gate orthogonal to the 0.75 lens score** — NOT folded
into a lens (folding would let a high average mask a missing-AC-coverage blocker).
`run_gates()` runs the readiness checks and attaches a `ReadinessReport` to
`PlanReview`. Waivers reuse the approval hash-binding pattern: a waiver is bound to
`content_hash`; editing the plan flips `valid=False` and re-blocks.

**New models** (`plan/review/readiness/models.py`):
- `ReadinessCheckResult{check_id, title, status(pass|fail|not_applicable|skipped), severity, hard, waivable, detail, remediation, citations[], evidence{}}`
- `Waiver{check_ids[], reason, waived_by, waived_at, plan_hash, valid}`
- `ReadinessReport{plan_id, plan_hash, results[], waivers[], generated_at}` with
  `hard_failures()`, `unwaived_hard_failures(plan)`, `is_ready(plan)`, `revalidate(plan)`.

**Check catalog** (`plan/review/readiness/checks.py`, `@check` registry):

| check_id | verifies | hard? | waivable | remediation |
|----------|----------|-------|----------|-------------|
| `criteria-present` | ≥1 explicit Criterion (detects decomposer title-fallback) | HARD | yes | Add explicit ACs |
| `ac-child-coverage` | every Criterion maps to ≥1 child | HARD | yes | Decompose so each AC has a child |
| `ac-testable` | each AC measurable (verb+object, no placeholder/TODO) | ADVISORY | yes | Rewrite vague ACs |
| `access-granted` | no `AccessRequirement.granted is False` | HARD | yes | Grant IAM action or rescope |
| `access-verified` | flags `granted is None` (couldn't simulate) | ADVISORY | yes | Confirm perms manually |
| `enrichment-integrity` | no adapter `available:false`/`error` on cloud-relevant plan | HARD | yes | Fix adapter creds, or waive "no cloud" |
| `decompose-trustworthy` | decompose didn't silently fall back | HARD | yes | Re-run; inspect recorded error |
| `deps-sound` | no dangling/self deps (`EpicPlan.validate_dependencies()`) | HARD | dangling: yes / cycle: **no** | Fix `depends_on` |
| `no-blocking-findings` | no review `Finding(blocking=True)` (e.g. secrets) | HARD | **no** | Resolve blocking finding |
| `children-present` | epic has ≥1 child | HARD | **no** | Plan undecomposable; revise |

**Graceful degradation (critical):** distinguish `not_applicable` (no cloud
relevance / adapters disabled — never blocks; air-gapped safe) from `fail` (adapter
ran and errored on a cloud-relevant plan) from `pass` ("verified absent"). Reuse the
cloud-relevance heuristic already in `service._enrich` — extract to a shared helper so
check and enrich agree.

**Closing gap #6 (needs a recorded signal):** extend `EpicPlan` with
`decompose_method: Literal["heuristic","llm","llm_fallback"]` + `decompose_errors[]`;
`decompose_with_llm` records `llm_fallback`+error instead of silently returning the
heuristic. Record swallowed template-check errors on a new `PlanSession.warnings[]`.

**Waiver mechanism** (`plan/review/readiness/waiver.py`): `waive(review, plan, *,
check_ids, reason, waived_by)` — precondition: each named check is currently `fail`
AND `waivable` (else `WaiverError`). Appends a `Waiver` bound to `plan.compute_hash()`.
`approval.approve()` precondition extended: refuse if `unwaived_hard_failures` remain
— so the human must fix or explicitly waive each hard failure before sign-off.

**`ready_to_emit()` change** (`plan/review/models.py`): add `readiness:
ReadinessReport | None`; new optional `plan` arg:
`ready_to_emit(plan=None) = base AND (readiness.is_ready(plan) if readiness else True)`.
Zero-arg back-compat preserved for existing callers/tests. `emit_to_github` and
`service.emit` pass `session.plan`.

**Surfacing:** meta block (`emit/labels.py`) gains a `readiness:` section
(`readiness_passed`, per-failed-check `waived_by`/`reason`/`check_id`) + a
`readiness:waived` label when any waiver present. No new SessionStatus (avoid kanban
contract churn) — surface readiness as a sub-state inside `human_review`; add a portal
readiness panel + `POST /{session_id}/waive` route and a CLI `waive` subcommand.

**Files:** modify `review/models.py`, `review/gates.py`, `service.py`,
`emit/labels.py`, `decompose/models.py`; add `review/readiness/{__init__,models,checks,waiver}.py`
+ web route.

---

## Workstream 2 — AIFactory handover contract v2

**Additive + versioned.** v1 fields stay at the top level (AIFactory needs zero
changes to keep working); v2 adds `contract_version`, `epic_context`, `child` under
keys AIFactory currently ignores. `build_requirements(child, *, plan, epic=None,
review=None, contract_version=None, ...)` returns the exact v1 dict when version 1.

**v2 `epic_context`:**
- `build_order[]` + `dependency_graph{key:[deps]}` — topo-sort over `child.depends_on`
  (reuse `_cycle_keys()` DFS; refuse v2 emit on cyclic/dangling).
- `feasibility{cost, effort, access{granted[], denied[]}}` — 1:1 from
  `EpicPlan.cost_estimate/effort_estimate/access_requirements`; `granted is None`
  (unverified) dropped to avoid a false guarantee.
- `constraints[]` — **sanitized** `InfraSnapshot` (see below).
- `knowledge_links[]` — `Citation`s + `enrichment.knowledge`, deduped + capped.

**v2 `child`:** `key, kind, depends_on, complexity, definition_of_done[]` (promotes
`acceptance_criteria` to a first-class machine list), `citations[]` (child-relevant subset).

**Include / Exclude:**

| INCLUDE | EXCLUDE (never crosses boundary) |
|---------|-------------------------------|
| title/description/metadata (v1) | secrets / credentials |
| build_order, dependency_graph | raw `InfraSnapshot.raw` (instance/cluster/SG dumps) |
| sanitized constraints[] | ARNs, account/subscription/project ids, instance/SG ids |
| knowledge_links[], per-child citations | IPs / CIDRs / DNS names |
| feasibility cost/effort | `InfraSnapshot.load` (live utilization, transient) |
| feasibility access granted/**denied** | PFactory scoring internals (aggregate_score, thresholds, blocking flags) |
| definition_of_done[] | content_hash, ingested_at, raw_text |
| child key/kind/complexity/depends_on | unverified access (`granted is None`); un-approved plans |

**Sanitizer** (`plan/emit/handoff_sanitize.py`, pure, never raises):
drop `raw`/`load`/`target`; keep `adapter`/`available`/`error`(≤200ch); resources→only
`regions`+`instance_types` shape + `*_count`; policies→categorized `policy_flags`
(drop raw rules/ids/CIDRs); findings→`notes` run through a **redactor regex**
(account ids `\d{12}`, `arn:…`, `i-…`, `sg-…`, IPv4/6/CIDR, `AKIA…`/long hex → `[redacted]`),
also redact all retained strings. **Size caps:** constraints ≤8, notes ≤10×200ch,
instance_types ≤20 keys, knowledge_links ≤25, cost.lines ≤25, assumptions ≤10, whole
`epic_context` ≤32 KB (drop in priority order + set `truncated:true`).

**Gating:** env `PFACTORY_AIFACTORY_CONTRACT_VERSION` (default `1`).

**Files:** modify `emit/aifactory_handoff.py`, `feasibility/run.py`,
`decompose/models.py`; add `emit/handoff_sanitize.py`.

---

## Workstream 3 — Validated handshake

**PFactory-side only** where possible (no hard dependency on new AIFactory endpoints).
New `handshake_create(payload, *, base_url, project_id, http, dry_run=True, retries=2)`
→ `HandshakeResult{ok, dry_run, task_id, correlation, attempts, mismatches[], warnings[]}`:
1. **Pre-flight:** if `review` supplied, refuse unless `review.ready_to_emit()` (excludes un-approved plans from ever being POSTed).
2. **POST** create-and-run (reuse `trigger_api`); capture `task_id`.
3. **Read back** `GET /api/tasks/{task_id}` (extend `HttpClient` Protocol with `get`).
4. **Assert intact:** title, `metadata.complexity`, `githubIssueNumber`,
   `requireReviewBeforeCoding`; persist `handoff_correlation.json` {spec_id, issue, task_id, project_id}.
5. **On mismatch:** structured `mismatches[]` + retry create (backoff); after exhaustion `ok:false`.

**Coordination dependency — AIFactory#317:** strict read-back needs AIFactory to
return a stable `task_id` and expose `GET /api/tasks/{id}` echoing persisted fields.
Until then, degrade to a "create-confirmed" handshake (2xx + non-empty task_id) with
warning `handshake: read-back unavailable (AIFactory#317 pending)`; gate strict mode
behind env `PFACTORY_AIFACTORY_HANDSHAKE` (default off).

**Files:** modify `emit/aifactory_handoff.py`.

---

## Prioritized backlog (epic-ready)

**P0 — correctness/governance core (the "bulletproof" minimum)**
- P0.1 `ReadinessReport`/`ReadinessCheckResult`/`Waiver` models + `review/readiness/` package `[M]`
- P0.2 Check catalog: `criteria-present`, `ac-child-coverage`, `children-present`, `deps-sound`, `no-blocking-findings` `[M]`
- P0.3 Wire `run_gates`→attach report; `ready_to_emit(plan)` change; emit passes plan `[S]`
- P0.4 `access-granted` hard check + `enrichment-integrity` (with not_applicable/skipped/fail/pass semantics + shared cloud-relevance helper) `[M]`
- P0.5 Waiver mechanism + `approve()` precondition + hash revalidation `[M]`
- P0.6 Tests: per-check pass/fail, waiver flow, hash-invalidation, e2e process→fail→waive→approve→emit `[L]`

**P1 — handover fidelity + verification**
- P1.1 `decompose_method`/`decompose_errors` signal + `decompose-trustworthy` check + `PlanSession.warnings` `[M]`
- P1.2 Contract v2 schema in `build_requirements` (epic_context: build_order, deps, feasibility, DoD) `[M]`
- P1.3 `handoff_sanitize.py` (redactor + caps) + `constraints[]`/`knowledge_links[]` `[M]`
- P1.4 Validated handshake (PFactory-side read-back, correlation, retry) behind env flag `[M]`
- P1.5 Meta-block `readiness:` section + `readiness:waived` label `[S]`
- P1.6 Tests: v2 schema/topo, sanitizer redaction+caps, handshake success/mismatch/retry/fallback/dry-run, backward-compat byte-identical v1 `[L]`

**P2 — UX, advisories, polish**
- P2.1 `ac-testable` + `access-verified` advisory checks `[S]`
- P2.2 Portal readiness panel + `POST /{session_id}/waive` route + CLI `waive` `[M]`
- P2.3 AIFactory#317 coordination (stable task_id + GET echo) → flip handshake to strict `[external]`
- P2.4 Docs: `guides/` readiness-gate + handover-contract-v2 pages `[S]`

Effort: XS≤½d · S 1–2d · M 3–5d · L 1–2wk.

## Verification (end-to-end)
- `apps/backend/.venv/bin/pytest tests/test_readiness_*.py tests/test_emit*.py tests/test_plan_service.py -v` — all new + existing green.
- Manual: ingest a plan with one AC uncovered + a denied IAM action → process → confirm
  `ready_to_emit` is False, readiness panel shows the two hard failures → waive with reason →
  approve → dry-run emit shows `readiness:waived` label + meta `readiness:` block.
- Handshake: point at a stub AIFactory; confirm create→read-back→correlation file written;
  inject a title mismatch → confirm retry + `ok:false`; disable read-back → confirm graceful
  "create-confirmed" fallback warning.
- Backward-compat: with `PFACTORY_AIFACTORY_CONTRACT_VERSION` unset, `requirements.json` is
  byte-identical to today (existing `test_emit` v1 assertions unchanged).

## Notes / non-goals
- Keep deterministic-first: every readiness check is pure code, no LLM.
- Do not add a new SessionStatus (avoid kanban churn) — readiness is a sub-state of `human_review`.
- Air-gapped / no-cloud-creds must remain shippable via `not_applicable`, never a permanent block.
