---
layout: post
title: "The plan is where the money is decided"
subtitle: "How the planning stage sets the routing policy the rest of the factory spends against, and why a cost number only means something if planning is honest first."
date: 2026-07-13 12:00:00
author: DataSeek Team
---

PFactory is the Plan stage of the Factory pipeline. It takes an idea, reads the
code that already exists, and produces a signed Task Contract with explicit
acceptance criteria before a single line is written. This cycle made something
clear that had been implicit: the plan is not just what gets built, it is also
what it will cost to build.

## Why planning owns the cost decision

Across the fleet this cycle we shipped RFC-0014, cost-aware model routing. The
headline result, measured on the coder, was a 55 percent cost reduction from
routing each stage to an appropriately sized model rather than sending everything
to the most expensive one. That saving is real, but the decision that drives it
is made upstream, here, at plan time.

The Task Contract carries a routing block: a per-stage request for how capable a
model each stage should get, keyed to the difficulty of the work. Planning is
where a task is classified, so planning is where the tier is chosen. AIFactory
then honours that request, floored so a genuinely hard task can never be
downgraded below the capability it needs. The router is the mechanism; the plan
is the intent.

We also kept the mechanical roles honest about which provider does the work, so
that a cost policy is a deliberate choice rather than an accident of defaults.

## What this proves

That cost control in an autonomous factory is a planning problem, not a billing
afterthought. You do not save money by capping a meter. You save it by deciding,
before the work starts, how much capability each part of the work actually
warrants, and by writing that decision into a contract the downstream services
are obliged to honour.

And it proves the value of separating planning from execution at all. The rest of
the industry collapsed the two together; the plan became whatever the agent
happened to do. Pulling planning back out gives you a place to put a governance
gate, an acceptance criterion, and now a cost policy, all before the expensive
part begins.

## What is next

Tighten the loop between the difficulty classification and the routing tier so
the plan can reason explicitly about the cost and capability trade-off, and feed
the measured outcomes from real runs back into how tasks are classified. The plan
should get better at spending exactly as much as a task is worth, and no more.
