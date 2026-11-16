---
status: approved
issue: 998
author: olafkfreund
---

# Intent: build and test results reach the planner

## Problem

PFactory receives the completion events AIFactory (build) and TFactory (verify) emit for work it handed off, but only reconciles a status and keeps the event in a process-lifetime registry (`apps/web-server/server/routes/contract_events.py`, `apps/backend/plan/emit/contract_sync.py`). Restart loses it. Usage, cost, the reason a unit failed and the acceptance criteria TFactory did not verify are never read. Nothing in decompose, feasibility or synthesize consults it, so the next plan for the same repository is written without knowing what was handed back, what went unverified, or how real spend compared with the estimate.

## Proposed outcome

- A completion event carrying a failure, an unverified criterion or a usage block is stored on the plan session and survives a restart (wherever plan sessions already persist).
- The plan record shows handback count, unverified criteria, and actual versus estimated cost.
- Decompose receives a short outcome summary for the target repository (recent handback reasons, unverified-criteria count, estimate versus actual cost) in its prompt.
- With no history, planning is byte-for-byte what it is today.

## Affected users and systems

PFactory only: `contract_sync.py`, the contract-events route, `PlanSession`, `PlanService._decompose`, the decompose prompt. The events carry what is needed when the emitter has it (AIFactory `usage` and `halt_reason`; TFactory `verification.traceability[]`, `halt_reason` and an explicit `outcome`), so AIFactory, TFactory and `shared/factory-contracts` need no change.

## Constraints

- Reuse the existing completion events and the existing endpoint; no new transport, no new contract fields.
- Missing data is unknown, never zero: an event without `usage` records no cost, without traceability records no coverage claim.
- A failure for feedback is a normalized failure: the existing status classification, or an explicit `outcome: failure` on the event (TFactory can emit `status: triaged` with `outcome: failure`). This is read by the feedback record only; the reconciler's own outcome and handback rules stay unchanged.
- Additive: old persisted sessions load; the reconciler's outcome and handback rules do not change.
- The estimate is PFactory's monthly cloud-cost estimate; the actual is build/verify token spend (`usage.cost_usd`). They are different quantities and are shown side by side, not presented as one budget.
- Reasons are free text from other services: bounded in length and count before they enter a prompt.
- Wave 3 work may be merging into PFactory: keep the diff tight.

## Open questions

None.
