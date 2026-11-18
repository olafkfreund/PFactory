---
status: approved
issue: 1083
intent: intent/2026-11-18-1083-pf-redteam-must-not.md
---

# Spec: red-team lens must/must-not contradiction check

## Design

In `apps/backend/plan/review/lenses/red_team.py`:

1. Remove `("must not", "must ")` from `_CONTRADICTION_PAIRS`.
2. Match the remaining pairs with whole-word regexes (`\b<pair>\b`) instead of `in`, so "asynchronous" no longer matches "synchronous".
3. Add `_MUST_RE = re.compile(r"\bmust(\s+not)?\s+((?:(?!\bmust\b)[^.;,\n])+)")` (the object stops at the next `must`; a trailing "and"/"or" is stripped after normalising) and a check in `_contradictory_acs`: over the lower-cased criteria, collect the whitespace-normalised object clause after `must` and after `must not`; every object present in both sets yields the same high "Contradictory acceptance criteria" finding, worded `must X` and `must not X`.

The check stays in the existing `_contradictory_acs`; severity, penalty and threshold are unchanged.

## Alternatives rejected

- Only change the pair to a regex (`\bmust(?! not)\b`): still flags any plan with an unrelated "must do A" and "must not do B", which is not a contradiction.
- Semantic or LLM comparison: not deterministic, out of proportion.
- Drop the must check: loses the one genuine contradiction case.

## Risks

The object comparison is exact after normalisation, so a paraphrased contradiction is missed (false negative). This is the safer direction for an approval gate that was blocking valid plans. No host impact.

## Verification

`tests/test_red_team_lens.py`: only "must not" passes the check; "must be public" with "must not be public" fails; unrelated must and must-not pass; "The cache must expire and must not expire" fails; "asynchronous" alone passes; "synchronous" and "asynchronous" both present fails. Existing public/private test unchanged.
