---
layout: post
title: "The bill arrives before the build does"
subtitle: "A plan costs real money to produce — detect, decompose, synthesize, govern — and most of that is spent before anyone approves it. PFactory now reports a plan session's cost as a running figure the moment it is ready for review, not only when it reaches a terminal outcome. So a plan you park, or abandon, no longer shows up as a free $0."
date: 2026-06-23 12:00:00 +0000
author: Olaf Freund
---

Here is a small dishonesty that is easy to ship by accident: report cost only when
something *finishes*. It feels right — bill at the end. But a planning engine does most
of its spending up front, and a plan does not have to finish to have cost money. This
week PFactory stopped pretending those plans were free.

## The gap: `$0` for work that wasn't free

PFactory's completion notification fired only on a **terminal** outcome —
`emitted` or `rejected`. That made sense for the final word on a plan. But a plan
session runs a full LLM pipeline before it gets anywhere near terminal: it detects what
the spec is asking for, decomposes it into children, synthesizes the contract blocks, and
runs the governance gates. By the time the session reaches **`processed`** — pipeline
done, parked, awaiting a human's approve-or-reject — the money is already spent.

If that human took the weekend, or walked away, the session never reached a terminal
event, so it never reported its cost. CFactory's cockpit showed PFactory at `$0` — "not
instrumented yet." Which was, politely, fiction. The plan cost what it cost the moment the
pipeline ran; the only thing missing was the report.

## The fix: emit cost when it is *accrued*, not when it is *final*

Cost is now treated as a running figure. The moment a session reaches `processed`,
PFactory emits a **non-terminal usage snapshot** —
`plan.completion.emit_usage_snapshot` — carried on the *same* signed completion envelope
as the terminal events, stamped with the session's current status and its accrued token
usage. CFactory records usage from any event that carries it, terminal or not. So:

- A plan parked awaiting approval shows its planning-and-gate cost **now**, not never.
- A plan abandoned before approval still reports what it spent getting that far.
- A plan that is later approved or rejected just updates from a figure that was already
  honest, instead of materializing one out of nowhere at the end.

It is best-effort and a no-op when there is no usage to report, so it adds a true number
or it adds nothing — it never invents one.

## Consistent across the fleet

This is deliberately the PFactory mirror of the running-cost emit AIFactory already does
on the build side. The point of matching them is that one plan's cost reads the same way
wherever you look: planning cost from PFactory, build cost from AIFactory, both as running
figures on the same envelope, both surfaced by CFactory. TFactory's verify-stage parity is
the next piece, and then every stage of a unit of work reports its cost the same way.

## The honest line on the number itself

A money figure only appears for **metered** billing — a real API or managed-cloud rate.
On a subscription or a local model, the snapshot reports **tokens and wall-clock time**,
not a notional dollar amount. We could multiply tokens by some made-up price and print a
confident-looking `$`, but a fabricated cost is worse than an honest "here is the usage,
you know your rate." Cost transparency is only transparency if the number is real.

The plan you are about to approve already has a price. Now you can see it before you say
yes — which is, after all, the entire point of making the plan the thing you review.

See the [Planning and Trust](/planning-and-trust/) guide for where this sits in the emit
pipeline.
