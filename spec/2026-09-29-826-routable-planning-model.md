---
status: draft
issue: 826
intent: intent/2026-09-29-826-routable-planning-model.md
---

# Spec: an unusable model is not a candidate

## What the measurements settled

| Question | Measured answer |
| --- | --- |
| Why was `gemini` chosen? | `cheapest_capable_model` (`cost_router_core.py:184`) filters on three predicates — `_serves_role`, `_meets_floor`, `_under_ceiling` — then sorts by cost. `gemini` serves `planning`, is `class=balanced`, and at 1.25/10 undercuts `claude-sonnet-5` at 3/15. Nothing asks whether it runs. |
| Is cross-provider shopping already restricted anywhere? | **Yes, and for this exact failure class.** `_MECHANICAL_ROLES = ("coding", "qa", "test_gen")` are pinned to `_MECHANICAL_PROVIDER = "claude"` so a cost tie can never resolve to another provider (AIFactory#779). **`planning` is the one routed role left free to cost-shop** — and it is the most consequential of the four. |
| What does the contract then do downstream? | It is authoritative: TFactory copies `execution.phase_models` into the spec's `task_metadata.json` so verify lanes use the build's models, and RFC-0014 precedence puts it above every downstream default. |
| How did it fail? | The Gemini CLI terminated the session; TFactory's guard refused to record a planning phase with no observed model id, and the whole Test lane failed with 21/21 subtasks built. |
| Are the catalog's ids current? | **No.** It routes to `claude-sonnet-4-6` and `claude-sonnet-4-5-20250929`; `sonnet` is now `claude-sonnet-5` and `opus` is `claude-opus-5-5` (AIFactory#1626). |

## Design

### 1. Availability is a property of the catalog entry

Each entry may carry `"available": false` with `"unavailable_reason"`. A fourth
predicate in `cheapest_capable_model` drops such entries before cost is
considered, exactly as the floor does. Absent means available, so every existing
entry and every injected test catalog behaves as it does today.

`gemini` is marked unavailable with the reason and the date — the CLI is
sunsetting on 2026-06-18 for personal tiers and terminates the session today.
The entry, its class and its price stay, so when the Antigravity migration makes
it work the change is one field, and the record of *why* it was disabled does
not have to be reconstructed. Deleting the entry would lose exactly that.

### 2. Planning stops cost-shopping across providers

`planning` joins the roles restricted to the `claude` provider. The precedent is
`#779`, which did this for **mechanical** roles — agentic code and test writing —
on the grounds that a cost tie must not silently change provider. Planning is
the role whose output governs every other phase, so the argument applies with
more force, not less.

This is deliberately separate from availability: if it were only the flag, the
next cheap-but-broken entry would be picked again the moment someone adds one.
The flag stops *this* failure; the restriction stops the *class*.

### 3. The catalog's model ids match the fleet

`claude-opus-5-5` is added and `claude-sonnet-5` kept; the superseded
`claude-sonnet-4-6` and `claude-sonnet-4-5-20250929` stay in the catalog as
pinnable entries but are no longer the cheapest at their class, so the router
resolves `planning`/`qa` to Opus 5.5 and `coding`/`test_gen` to Sonnet 5 —
matching the defaults set in AIFactory#1626, so the contract stops contradicting
the fleet policy it overrides.

### 4. The rationale records what was rejected

`_rationale` gains the skipped entries: `"gemini skipped: unavailable"`. Today
the routing block names only the winner, which is why diagnosing this needed
three services' logs rather than the contract that caused it.

## Alternatives rejected

- **Delete the `gemini` entry.** Smallest diff; loses the price, the class and
  the reason, so re-adding it later is a guess and the next reader cannot see
  that it was deliberate.
- **Probe reachability at routing time.** Authoritative, and it would catch the
  next provider to break silently — but it puts a network call on the planning
  path and makes contract emission depend on a third party being up. Better as
  a scheduled job that maintains the flag (follow-up).
- **Only restrict planning to claude, without the flag.** Fixes the observed
  case and leaves an unusable model selectable for any other role.
- **Only add the flag, without the restriction.** Fixes today and not the class.
- **Raise the planning floor to `frontier`.** Would dodge `gemini` by accident
  (it is `balanced`), not by reason, and would change cost for every plan.
- **Weaken TFactory's model-evidence guard** so a dead session does not fail the
  lane. That guard is what made this visible at all; a lane that "passes"
  without a model is the failure this fleet keeps finding.

## Risks

- **A signed contract's routing changes**, so plans emitted after this land
  choose different models than before. That is the point, but the routing block
  is read by humans approving cost, so the rationale must show the change rather
  than have it appear silently — hence design 4.
- **Cost rises** for `planning`: Opus 5.5 at 5/25 against gemini at 1.25/10, on
  a role estimated at 40k in / 20k out. The previous figure was for a model that
  produced nothing, so the honest comparison is against a failed run, not a
  cheap one — but the ceiling logic must still be checked, because a raised
  planning cost can now trip the per-role ceiling relaxation.
- **`available: false` could hide a model someone deliberately pinned.** A
  contract-pinned model bypasses the router entirely (RFC-0014 precedence), so a
  pin still works; only auto-selection is affected. Worth asserting in a test.
- **The vendored catalog is shared.** `_CATALOG_PATH` loads a hub-vendored file;
  if the same catalog is consumed elsewhere, the new field must be ignorable by
  older readers — it is, being additive and absent-means-available.

## Verification

Deterministic:

1. `cheapest_capable_model` skips an entry with `"available": false` even when
   it is the cheapest that serves the role and meets the floor. **Mutation:**
   remove the predicate and the test must fail.
2. An entry without the field is still selectable — no existing behaviour
   changes by omission.
3. `select_phase_models` on a representative contract returns
   `planning = claude-opus-5-5`, `coding = claude-sonnet-5`, and never `gemini`.
   **Mutation:** take `planning` out of the provider-restricted set and, with
   the flag also removed, the test must fail.
4. The rationale string names the skipped entry and its reason.
5. A contract carrying a pinned model still gets that model, unavailable flag or
   not — the pin outranks the router.
6. The cost estimate and ceiling behave: a plan whose planning role now costs
   more does not silently drop a role (the `pick is None` path) but records a
   ceiling relaxation.

Live:

7. Re-emit the MyFriends contract and read `execution.phase_models` — it names
   Opus 5.5 for planning where it named `gemini`, with the rejection in the
   rationale.
8. Drive that contract to TFactory and confirm its planner reaches a model:
   the spec's `task_metadata.json` carries the new planning model and the Test
   lane produces a verdict rather than `planner_session_never_ran`.

Gates: ruff, `ruff format --check` over the repo's CI path list,
`ratchet_lint.py --base origin/main` with its `--package` flags, and the full
suite.
