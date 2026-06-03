---
layout: post
title: "Your tests found a bug. Now what?"
subtitle: "Finding the problem was never the hard part. PFactory now hands the fix back to AIFactory, re-tests, and knows when to stop — plus two more things that shipped this cycle."
date: 2026-06-03 12:00:00
author: DataSeek Team
---

A test that fails is only half a result. The other half is the work it
implies — someone has to read the report, find the code, write the fix, and run
the suite again. For all the talk of autonomous testing, that last mile usually
lands back on a human at 5pm on a Friday.

PFactory generated tests, ran them in a sandbox, and scored them against five
signals. When something failed, it wrote you an honest report and stopped. Good
report. Dead end. This cycle we closed the loop.

## The loop, closed

PFactory's sister project, **AIFactory**, is where the feature was planned and
built in the first place. So when PFactory's tests find a real problem, the fix
belongs there — not in a ticket. v0.5.0 wires the two together both ways:

```
AIFactory builds → /handover-to-pfactory → test → (fail)
   → /handback-to-aifactory → AIFactory QA Fixer → re-test → green
```

When a run finishes with failing tests, the **Triager** packages the failures
into a correction request — the failing tests, what they observed, the
acceptance criterion each maps to — shaped exactly like the fix request
AIFactory's **QA Fixer** already knows how to read. Hand it back, and AIFactory
writes the fix on the *original* spec. No new ticket, no lost context, one
thread.

## Bounded, so it can't run away

Autonomous fix loops have an obvious failure mode: fix, re-test, still red, fix
again, forever — burning agent runs on a problem it can't solve. So the loop is
bounded. `/pfactory-fixloop` runs one cycle, then stops on one of three
verdicts:

1. **passed** — no failing tests remain. Done.
2. **stuck** — the correction-cycle cap (default 2) is hit, *or* the same tests
   keep failing after a correction (no progress). A human takes over.
3. **retest** — there's progress and headroom, so it hands back and runs again.

The same rule the Planner already uses for replans — `give up after N, ask a
human` — now governs the fix loop too.

## Dry-run first, always

Handing a task to another agent is an outward-facing action, so it follows the
same posture as everything else in PFactory: **prepare locally, send only on
opt-in.** The correction artifact is always written to the workspace; actually
sending it is gated behind `PFACTORY_HANDBACK_SEND=1` or an explicit
`--send`/confirm. No automatic pushes, no surprise agent runs.

```
# preview what would go back — writes nothing remote
python -m agents.handback <spec_dir>

# send it, for real, when you mean it
python -m agents.handback <spec_dir> --send
```

## Two more things that landed

While the loop was the headline, two adjacent addons shipped alongside it:

- **Visual Inspection Run.** For UI-heavy features and SaaS targets, PFactory
  records a real Playwright browser run — trace, video, and step-labelled
  *verification and error* screenshots — then packages a human-readable report,
  a correction plan, and a GitHub issue export into
  `automated-test/<datetime>/`, committed to the repo. You get evidence, not
  adjectives.
- **Cloud Reports.** A read-only posture assessment for **AWS, GCP and Azure** —
  discover the account, scan with Prowler against CIS, draw the topology, and
  emit an accept / flag / reject verdict with a remediation plan. It's cloud
  *misconfiguration* review, kept deliberately separate from the app-code lanes.

## A real run

You merge a login feature in AIFactory and hand it to PFactory. The api lane
generates a test asserting `200` on valid credentials; it comes back red —
`500`, an unhandled null. Instead of filing a bug, you run
`/handback-to-aifactory`. AIFactory's QA Fixer reads the failure, patches the
handler on the same spec, and signals done. PFactory re-tests: green. Two
commands, one thread, no context lost between them. If it had stayed red after
two tries, the loop would have stopped and told you so — plainly — rather than
churning.

That's the whole point. The machine should do the loop it can close, and know
the difference when it can't.

See [the architecture](/architecture/) for how the agents fit together, or
[the demos](/demos/) for the handover in motion.
