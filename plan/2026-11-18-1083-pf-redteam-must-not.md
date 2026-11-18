---
status: draft
issue: 1083
spec: spec/2026-11-18-1083-pf-redteam-must-not.md
coders:
  1: codex   # small tricky logic in one file
reviewers:
  - codex    # read-only review of the final diff
---

# Plan: red-team lens must/must-not contradiction check

Approved decisions (spec): drop the self-matching `must not`/`must ` pair; whole-word regex for the other pairs; contradiction of must/must-not only when the object clause is the same; severity/penalty/threshold unchanged.

## Steps

1. `apps/backend/plan/review/lenses/red_team.py` (`_CONTRADICTION_PAIRS` ~l.65-72, `_contradictory_acs` ~l.183): remove the must pair, use `re.search(rf"\b{re.escape(x)}\b", joined)` for both halves, add `_MUST_RE` (object stops at the next `must`, trailing and/or stripped) and the object-clash detection described in the spec. → verify by `apps/backend/.venv/bin/pytest tests/test_red_team_lens.py`
   Traps: ruff.toml line length; keep the file's existing finding title.
2. `tests/test_red_team_lens.py`: add the six cases listed in the spec (Verification). Done by the lead. → verify by the same command, and the new tests fail on the unfixed `red_team.py`.
   Traps: tests use `_plan(criteria=[...])` and `_run`.

## Tests

`apps/backend/.venv/bin/pytest tests/test_red_team_lens.py tests/test_lens_gating.py`, then `ruff check` and `ruff format --check` on the two files.

## Rollback

Revert the commit; the lens returns to the old (over-flagging) behaviour.
