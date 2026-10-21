---
status: approved
issue: 826
spec: spec/2026-09-29-826-routable-planning-model.md
---

# Plan: an unusable model is not a candidate

## Approved decisions (self-contained)

- **Why.** The MyFriends demo produced 42 commits and 259 passing tests and its
  Test lane never ran. `cheapest_capable_model` filters on `_serves_role`,
  `_meets_floor`, `_under_ceiling` and then sorts by price; `gemini` serves
  `planning`, is `balanced`, and at 1.25/10 undercuts `claude-sonnet-5` at 3/15.
  Nothing asks whether it runs. The signed contract carried
  `planning: "gemini"`, TFactory copied it into the spec, the Gemini CLI
  terminated the session, and TFactory's model-evidence guard correctly refused
  to record a planning phase that never reached a model.
- **Availability becomes catalog data**: `"available": false` +
  `"unavailable_reason"`, dropped by a fourth predicate in
  `cheapest_capable_model` before cost is considered. Absent means available, so
  existing entries and injected test catalogs are unchanged.
- **`gemini` is marked unavailable, not deleted** — the entry, class and price
  stay so the Antigravity migration is a one-field change and the reason
  survives.
- **`planning` joins the provider-restricted roles.** `_MECHANICAL_ROLES` are
  already pinned to `claude` so a cost tie cannot silently change provider
  (#779); planning governs every other phase, so the argument is stronger. The
  flag stops this failure; the restriction stops the class.
- **The catalog's ids match the fleet**: add `claude-opus-5-5`, keep
  `claude-sonnet-5`; the superseded 4-6/4-5 entries stay pinnable but stop being
  the cheapest at their class, so routing matches AIFactory#1626.
- **The rationale records rejections**, not just the winner — this took three
  services' logs to diagnose because the contract only named what won.
- **A contract-pinned model still wins** (RFC-0014 precedence): the flag
  disqualifies auto-selection, never an explicit pin.

## Steps

Branch `fix/826-routable-planning-model` off `main`. Four files edit, so under
the model-split rule this goes to a `coder` agent, step by step, with review by
a fresh agent against this plan.

1. **`cost_router_core.cheapest_capable_model`**: add an availability predicate
   beside the floor filter; absent field means available.
   → verify: an unavailable entry that is cheapest, serves the role and meets
   the floor is not returned; an entry without the field still is.
   **Mutation:** remove the predicate — the first test must fail.
2. **`model-catalog.json`**: mark `gemini` unavailable with its reason and date;
   add `claude-opus-5-5` (frontier, 5/25, the roles the other frontier entry
   serves).
   → verify: the catalog parses, and every entry still has the keys the loader
   requires.
3. **`cost_router._MECHANICAL_ROLES`**: add `planning` to the provider-restricted
   set, renaming it if the name no longer describes it, with the reason in the
   comment beside #779's.
   → verify: `select_phase_models` on a representative contract returns
   `planning = claude-opus-5-5`, `coding = claude-sonnet-5`, never `gemini`.
   **Mutation:** remove `planning` from the set AND the availability flag — the
   test must fail.
4. **`cost_router._rationale`**: include skipped entries as
   `"<id> skipped: <reason>"`.
   → verify: the rationale names gemini and its reason.
5. **Pin the precedence**: a contract carrying a pinned model gets that model
   even when the catalog marks it unavailable.
6. **Check the ceiling path**: planning now costs 5/25 on a 40k/20k role, so
   assert a plan does not silently drop the role — the `pick is None` branch —
   and records a ceiling relaxation when it applies.
7. **Gates:** ruff, `ruff format --check` over the CI path list,
   `ratchet_lint.py --base origin/main` with its `--package` flags, and the full
   suite.
8. **Live proof:** re-emit the MyFriends contract and read
   `execution.phase_models` — planning names Opus 5.5 where it named `gemini`,
   with the rejection in the rationale. Then drive that contract to TFactory and
   confirm its planner reaches a model and the Test lane returns a verdict
   rather than `planner_session_never_ran`.
9. **PR → `main`** with the before/after contract blocks; close #826.

## Deviations recorded during implementation

- **The spec's claim about superseded entries was wrong, and a step 5 was added
  to fix it.** Design item 3 said the superseded `claude-sonnet-4-6` /
  `claude-sonnet-4-5-20250929` entries would "stay pinnable but stop being the
  cheapest at their class". They do not: measured, they price **identically** to
  the current models —

  ```
  claude-opus-5-5    frontier 5/25     claude-opus-4-8    frontier 5/25
  claude-sonnet-5    balanced 3/15     claude-sonnet-4-6  balanced 3/15
  ```

  so it is an exact tie broken by sort order, and the older id wins.
  `select_phase_models` resolved `planning` to `claude-sonnet-4-6`, and to
  `claude-opus-4-8` for governed contracts — which defeats the part of this
  issue that was meant to stop the contract contradicting the fleet's model
  policy.

  Step 5 reuses the availability mechanism from step 1: the superseded Claude
  entries are marked `available: false` with a reason that distinguishes them
  from `gemini` — not broken, simply not current, and still selectable by an
  explicit pin. Caught by the coder's own test pinning `claude-opus-4-8`, which
  honestly recorded what the code did rather than what the spec asserted.

## Tests

```sh
V=apps/backend/.venv/bin
$V/python -m pytest tests/ -q -k "cost_router or catalog or phase_model or routing"
$V/python -m pytest tests/ -q
$V/ruff check apps/backend apps/web-server tests scripts
$V/ruff format --check <the repo's CI path list>
$V/python scripts/ratchet_lint.py --base origin/main \
  --package apps/backend --package apps/web-server --package scripts
```

Expected: each new test fails before its step and passes after; both mutations
fail; the re-emitted contract names a model the fleet can run.

## Rollback

Revert the PR. Routing returns to price-only selection and `gemini` becomes
selectable for planning again, so new contracts would once more pin a model that
cannot run — but nothing stored needs unwinding: the change only affects models
chosen for contracts emitted while it is live, and contracts already signed
carry their own pins.

- **Re-based on `dev` (2026-10-21).** The branch targets `dev`, not `main`, and
  the ratchet runs with `--base origin/dev`. On `dev` the catalog had no
  `claude-sonnet-4-5-20250929` entry, but had `claude-opus-4-8` (the frontier
  entry that ties `claude-opus-5-5` at 5/25). So the superseded entries marked
  `available: false` are `claude-opus-4-8` and `claude-sonnet-4-6`;
  `claude-fable-5` (10/50) is a different model and is left available.
- **`_MECHANICAL_ROLES` is renamed `_CLAUDE_ROLES`** and now holds all four
  routed roles, since planning joined it.
- **Step 5 (pin precedence)** is tested as: `apply_cost_routing` leaves an
  existing `execution.model` and keys the router does not own (e.g.
  `qa_fixer`) set to an unavailable model, and `estimate_cost` still prices an
  unavailable model. The router has no per-role pin input to honour beyond that.
- **Step 8 (live proof against MyFriends/TFactory) was not run** in this bulk
  lane; the regression is covered by `tests/test_cost_router.py`.
- Steps 1-6 were implemented by the Codex coder; gates (step 7) were run by the
  session.
- **Step 1 moved from `cost_router_core` to `cost_router`.** `cost_router_core.py`
  is a byte-exact vendored copy of the Factory hub canonical, and the
  verification-core drift gate rejects any local edit. So `select_phase_models`
  filters `"available": false` entries out of the catalog before
  `cheapest_capable_model` runs; the full catalog is still used for
  `estimate_cost`. Same behaviour for PFactory's router; other consumers of the
  hub core do not see the flag until the hub canonical gains it (follow-up).
