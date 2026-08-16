---
layout: post
title: "A plan that argues with itself should not reach the coder"
subtitle: "Three weeks on the planner: a gate that catches a worked example contradicting its own invariant, per-lens review verdicts instead of a single pass or fail, planning that reads five more languages, and two security criticals closed with a query that survives its own barriers."
date: 2026-08-10 09:00:00 +0000
author: DataSeek Team
---

PFactory turns an issue into a plan the rest of the line can execute. The failure
mode nobody catches by reading is a plan that is internally inconsistent: an
invariant stated in one section and quietly violated by the worked example three
paragraphs down. A human reviewer skims past it. A coding agent implements the
example.

The last three weeks were largely about making the planner disagree with itself
out loud, before anything downstream acts on the disagreement.

## The gate that reads the worked example

The headline change is a gate that blocks a plan whose worked example
contradicts its own invariant. It is a small idea with a large blast radius: if a
plan says "IDs are never reused" and then walks through an example reusing one,
the plan is not a specification, it is two specifications. Previously that
survived planning, survived review, and became a defect in someone's build.

Alongside it, plan review stopped collapsing to a single verdict. Reviews run
through several lenses, and the planner now publishes the **per-lens verdict**
rather than only `gates_passed`. A plan can be sound on structure and wrong on
testability, and the cockpit can now say which — and disable Approve on a
gate-blocked plan while naming the lens that blocked it. A single boolean was
hiding the one thing a reviewer needed.

Two related fixes come from the same instinct. Acceptance criteria that wrapped
across lines were being truncated at the wrap, so the second half of a criterion
silently vanished between ingest and plan. And knowledge grounding was being
applied to dimensions a plan did not actually have, which produced confident
enrichment about things the plan never mentioned.

## Planning that reads more of your code

Code-aware planning only helps for languages the recogniser can see. The
footprint miner now understands C#, Kotlin, PHP, Swift and C/C++ paths, so a
brownfield plan in those stacks gets the same treatment as a Python or TypeScript
one rather than falling back to generic advice.

Child-task synthesis got two corrections in the same spirit. A CI/CD child was
being scoped to the whole pipeline rather than the delta the change actually
touches, and a testing child was being pointed at a design document instead of at
test files. Both produced work that looked reasonable and was aimed at the wrong
target.

## Two security criticals, and a query that survives its own barriers

Static analysis found a command-injection path and an SSRF path. Closing them was
the easy half; proving the closure was the interesting half.

Standard analysis rules do not understand a project's own sanitisers, so a
correctly guarded path still reports as vulnerable and a genuinely fixed one
cannot be distinguished from a suppressed one. The fix is a barrier-aware query:
the analysis is told what the barrier is, so a path through it stops being a
finding for the right reason instead of being silenced. The command-injection
work was verified against a positive control — a deliberately unguarded path that
the query must still catch — because a query that reports nothing is
indistinguishable from a query that finds nothing.

The SSRF fix came with a smaller one worth naming: an email address was being
written to the logs in plain text. It was not the vulnerability anyone was
looking for, and it was a real data leak.

## Things that were quietly not controls

Several fixes this month removed things that looked like protection:

- A pod-disruption budget that allowed zero disruptions is not a control. It
  blocks routine maintenance and protects nothing.
- A pre-commit hook was rewriting files with an unpinned formatter, so two
  developers on different versions fought over the same lines.
- A test was writing a fake "gate skipped" notice into the real CI job summary.
  The test passed; the summary lied.
- The default runner image pointed at an image the pipeline did not build, so CI
  ran something other than what had been published. Runner images are now
  published, signed, and the pipeline runs what it published.

None of these were reported as bugs. They were found by looking at what an
artefact actually said, rather than at whether a step went green.

## Type errors that were defects, not backlog

Two rounds of type-checking work cleared 90 errors, and the split matters more
than the count. Most were suppressions and annotation debt. A handful were
genuine defects — including a wrong function signature — hiding in a pile
everyone had learned to scroll past. That is the argument for keeping the pile
small: a real defect in a list of known-noise findings is invisible.

## Tracing, honestly

PFactory exports OpenTelemetry traces now, and the pull request title states the
condition we held ourselves to: only claim it when a span lands. The first
attempt instrumented an application that was not the one actually serving
requests, which produced a clean configuration and no telemetry. The startup
probe also blocked startup for twenty seconds while waiting on the collector,
which is an availability problem introduced by an observability feature.

Both are fixed, and both are the same lesson the whole fleet learned this month:
configuration is not evidence.

## What is next

Multi-tenant git support means the planner now reconnoitres the tenant's own git
host rather than assuming a single provider, and PFactory serves the shared
agent-skills discovery endpoint alongside the rest of the fleet. Those are
foundations rather than finished features, and there is more to say when they
have run against real tenants rather than test ones.
