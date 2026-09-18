---
status: draft
issue: 725
author: Olaf Krasicki-Freund
---

# Intent: Live emit blocks the server and loses track of what it created

## Problem

A live emit of an approved 34-issue plan was SIGKILLed mid-run (exit 137) after
creating 27 issues. Afterwards the session still showed the previous dry run
(`dry_run: true`, `epic_number: null`) — no record that anything was created,
and a re-run would have duplicated all 27. Recovery was done by hand.

Two causes, confirmed in the code on `dev`:

1. **The emit runs on the event loop.** `POST /{id}/emit`
   (`apps/web-server/server/routes/plan_pipeline.py:526`) is an `async` handler
   that calls the synchronous `SERVICE.emit(...)` directly. Each issue is one or
   more `gh` subprocess calls (label bootstrap + create + sub-issue link), so a
   34-issue emit holds the loop for minutes. `/api/health` cannot answer; the
   liveness probe (`timeoutSeconds: 5`, `charts/pfactory/values.yaml:264`) fails
   and kubelet kills the pod. `/process` already avoids this with
   `process_async` → `asyncio.to_thread` (RFC-0016 #217); emit was never moved.
2. **Progress is saved only at the end.** Emit is already built to resume
   (#119): a re-emit reuses `emitted_issue_number` and
   `emit_result.child_numbers`. But `PlanService.emit`
   (`apps/backend/plan/service.py:1356`) writes those in one `_save` after
   `emit_to_github` returns, so a kill anywhere mid-loop loses every number and
   the resume logic has nothing to resume from.

A smaller defect from the same run: the "Set up testing" child carries
`handover:tfactory` (`plan/synthesize/testing_strategy.py:266`) as well as
`handoff:tfactory`. TFactory reads only `handoff:tfactory` (20 uses, 0 of
`handover`), so `handover` is the typo.

Already fine, no work needed: the issue's worry that emit assumes labels exist.
`GhCliRunner.create_issue` runs `_ensure_labels` (`gh label create --force`)
before every create (`plan/emit/gh_runner.py:90-128`); the failure in the issue
was from the manual `gh` recovery, not from PFactory.

## Proposed outcome

- A live emit of any size does not stop `/api/health` answering; the pod is not
  killed by its own emit.
- Each created issue number is on the session as soon as the issue exists. If
  the process dies mid-emit, the session shows a partial live emit (epic + the
  children created so far), and re-running emit creates only the rest — no
  duplicates.
- The testing child carries only `handoff:tfactory`.

## Affected users and systems

- Anyone emitting a plan live: portal, MCP `plan_*` tools, PARR conductor.
- `plan/service.py` (emit), `plan/emit/github_emitter.py`,
  `server/routes/plan_pipeline.py` (emit route),
  `plan/synthesize/testing_strategy.py`, tests.
- Deployed pod on the cluster (behaviour under the existing probe).

## Constraints

- Must stay a synchronous request for callers: same request/response contract
  for `/emit`, no new polling API.
- No change to what a successful emit creates (titles, bodies, labels other
  than the typo, sub-issue links).
- Dry run unchanged; it must not write progress as if it were live.
- No helm probe change needed or wanted — fixing the blocking is the real fix;
  loosening the probe would hide the next blocking call.

## Open questions

1. There is still a window of a second or so between GitHub creating an issue
   and PFactory saving its number. A kill exactly there leaves one duplicate on
   re-run. The issue suggests also skipping planned titles that already exist in
   the target repo. Include that check, or accept the one-issue window?
   Recommendation: accept it for now — a title search costs one extra `gh` call
   per child on every emit and can wrongly match an unrelated issue with the
   same title; the per-issue save shrinks 27 duplicates to at most 1.
2. `emit_contract` has the same sync-in-async shape (`plan_pipeline.py:548`) but
   makes one HTTP call, not N. Move it off the loop too while here?
   Recommendation: yes — same one-line change, same failure class.
