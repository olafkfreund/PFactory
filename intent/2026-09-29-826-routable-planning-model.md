---
status: approved
issue: 826
author: olafkfreund
---

# Intent: the plan only routes to models that can actually run

## Problem

A build can be fully planned, fully coded and fully tested by its own lane and
still end with **zero verification**, because the contract routed its planning
phase to a provider that cannot execute.

Measured on the MyFriends demo — 42 commits, 11,436 insertions, 259 passing
Kotlin tests — whose Test lane shows `planner_failed /
planner_session_never_ran`:

1. The cost router picks the cheapest model meeting each role's floor.
   `apps/backend/plan/emit/model-catalog.json` offers, for a `balanced` floor:

   ```
   gemini            class=balanced  price 1.25 / 10 per MTok
   claude-sonnet-5   class=balanced  price 3    / 15 per MTok
   ```

   so `gemini` wins on price.

2. The signed contract carries it:
   `"phase_models": {"planning": "gemini", "coding": "claude-sonnet-4-6", …}`,
   rationale `"tier=medium floor=balanced; class=premium; planning=gemini"`.

3. TFactory copies the contract's phase models into the spec's
   `task_metadata.json` — deliberately, so verify lanes use the build's chosen
   models.

4. The Gemini CLI then fails, and TFactory's model-evidence guard refuses to
   pretend otherwise:

   ```
   [session] SDK response stream terminated unexpectedly: Gemini CLI (yolo) error…
   Gemini CLI is sunsetting on 2026-06-18 for free / Pro / Ultra personal tiers
   planner: the session never reached a model — not retrying
   ```

Two things are wrong, and the second is the general one:

- **Stale entries.** The catalog still routes to `claude-sonnet-4-6` and
  `claude-sonnet-4-5-20250929`; `sonnet` now means `claude-sonnet-5` and `opus`
  means `claude-opus-5-5` (AIFactory#1626).
- **Availability is not part of routing.** Price is the objective; whether the
  chosen model can be reached is not a constraint anywhere. A cheap entry that
  fails 100% of the time is infinitely expensive, and nothing in the routing
  rationale records that the model never responded.

**Why this is urgent beyond one dead lane:** RFC-0014 precedence puts a
contract's pinned phase models **above** every downstream default. AIFactory's
defaults were just changed so Opus 5.5 plans and Sonnet 5 codes — and that
change is **inert for handoff-driven builds**, because this contract decides
instead. A fleet model policy that is not set here is not set at all.

## Proposed outcome

A signed contract names, for every role, a model the fleet can actually run; and
a model that cannot run is not offered for that role, rather than being chosen
because it is cheap. The fleet's model policy — Opus for judgement, Sonnet for
volume — is what handoff builds actually get.

## Affected users and systems

- `apps/backend/plan/emit/model-catalog.json` and the cost router that reads it
  (`cost_router.py`, `tier_profile.py`, `execution_profile.py`).
- Every downstream consumer of `execution.phase_models`: AIFactory's build and
  TFactory's verify lanes, which both treat the contract as authoritative.
- Not the routing *mechanism* — floors, tiers and the RFC-0011 difficulty rules
  stay as they are; this is about what the catalog offers.

## Constraints

- **Do not silently change what an existing plan would route to** without the
  rationale showing it: the routing block is part of a signed contract and is
  read by humans approving cost.
- **Cost still matters.** The answer is not "always the most expensive model";
  it is that an unusable model is not a candidate.
- **No behaviour that pretends.** TFactory's guard — refusing to record a phase
  whose session never reached a model — is correct and must not be weakened to
  make a dead provider look like a pass.
- Whatever lands must keep `gemini` usable if and when the Antigravity CLI
  migration makes it work again; this is a fact about today's runtime, not a
  judgement about the model.

## Open questions

1. **How should availability be expressed?** My recommendation: a field on the
   catalog entry (e.g. `"available": false` with a reason) that the router
   treats as disqualifying, so it is data rather than code and one place shows
   why. The alternative — deleting the entry — loses the reason and the price,
   and someone re-adds it later.
2. **Should the router prove reachability, or trust the flag?** A probe would
   be authoritative and would also catch the next provider to break silently,
   but it puts a network call on the planning path. I lean to the flag now and a
   scheduled probe that maintains it as a follow-up.
3. **Which models should the roles actually get?** I recommend matching the
   fleet policy set in AIFactory#1626 — Opus 5.5 for planning/qa, Sonnet 5 for
   coding/test_gen — so the contract stops contradicting the default rather than
   silently overriding it.
4. **Does the rationale need to record rejections?** Today it names the winner.
   Recording "gemini skipped: unavailable" would have made this diagnosable from
   the contract alone instead of from three services' logs.
