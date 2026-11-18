---
status: approved
issue: 1083
author: olafkfreund
---

# Intent: red-team lens must not flag every plan containing "must not"

## Problem

`apps/backend/plan/review/lenses/red_team.py` scans the joined criteria text for pairs of substrings and reports "Contradictory acceptance criteria (high)" when both appear. The pair `("must not", "must ")` always matches itself, because `"must "` is a substring of `"must not "`. Any criterion containing "must not" is therefore flagged. The lens scores 0.70 (below the 0.75 gate) and `plan_approve` returns 409. Sessions 231 (blocked) and 232 (approved) differ only by wording. The same substring defect exists in `("synchronous", "asynchronous")`: "asynchronous" contains "synchronous".

## Proposed outcome

- A plan whose criteria only say "must not ..." no longer fails the contradiction check (other checks may still apply).
- A genuine contradiction (`must X` and `must not X` on the same object) is still flagged, as are the other pairs when both whole words occur.

## Affected users and systems

Every plan reviewed with the red-team-review extension enabled; `plan_approve` callers.

## Constraints

- Smallest correct change, in `red_team.py` only, plus tests.
- Plain whole-word pairs (e.g. public/private) are kept as they are: a coarse heuristic, out of scope here. Only the two self-matching substring pairs are fixed.
- No change to severity, penalty or threshold behaviour.

## Open questions

None. (Decision, owner delegation 2026-11-18: compare the objects of the two clauses; plain co-occurrence of "must" and "must not" is not a contradiction.)
