---
status: draft
issue: 662
author: Olaf Krasicki-Freund
---

# Intent: Triager reports tests as committed when the git write failed

## Problem

PFactory's forked Triager (`apps/backend/agents/triager.py`) sets
`committed_count = len(committed)` (line 807), where `committed` is the set of
candidates triage **accepted**. Nothing reconciles it with the git write's
result (`git_writer.ok`). The report is also rendered (step 4, lines ~644-660)
*before* the git write (step 5), so it cannot know whether the write worked.

So a run whose git write failed — e.g. `checkout … is already used by worktree`,
measured live in TFactory — writes `committed_count: 5` to `status.json` and
prints a "Committed (accept) | 5" table, for tests that reached no branch. A
positive count for work that did not happen is the one shape nobody
double-checks.

The fork is live, not dead code: `agents/evaluator.py:455` schedules the
Triager after every evaluation, so deleting it is not an option.

TFactory fixed the same defect in PR TFactory#1261 (merged 2026-08-29). This is
the port.

## Proposed outcome

- `status.json` and `triage_report.json` carry two counts:
  `accepted_count` (what triage accepted) and `committed_count` (what landed).
  They differ exactly when a git write was attempted and failed; then
  `committed_count` is 0.
- A declared dry run (the default, `PFACTORY_TRIAGER_GIT_WRITE` unset) keeps
  today's numbers: `committed_count == accepted_count`, as in TFactory.
- The report is rendered after the git write. On a failed write it says
  "Delivery FAILED — nothing was committed", with the git error, and lists the
  accepted tests under "Accepted but NOT committed" instead of "Committed".
- No consumer loses a key: `committed_count` stays, `accepted_count` is added.

## Affected users and systems

- Operators reading `status.json` / the triage report / the PR comment built
  from it (`findings/pr_comment_body.md`).
- The completion webhook payload and handback trigger (they read status, not
  counts — unaffected).
- `apps/backend/agents/triager.py`, `apps/backend/agents/triage_report.py`, tests.
- No web-server or frontend code reads `committed_count` (checked).

## Constraints

- Default dry-run behaviour and output unchanged.
- Terminal `status` values unchanged (`triaged` / `triaged_empty` /
  `triager_failed`): the completion webhook, stage events and the handback
  trigger key on them. TFactory also left its status alone.
- Port, not redesign: keep TFactory's names and report wording so the two
  factories read the same.

## Open questions

1. TFactory also changed its terminal **outcome** to `failure` on a delivery
   failure (`_delivery_verdict`). PFactory's fork has no outcome layer — its
   only terminal signal is `status`. Options: (a) leave `status: triaged` and
   rely on `committed_count: 0` + `git_writer.ok: false` + the report heading;
   (b) set `status: triager_failed` on a delivery failure. Recommendation: (a)
   — (b) would make the handback trigger and `/pfactory-watch` treat a
   successful triage with a failed push as a triage crash, and it is not what
   TFactory did.
