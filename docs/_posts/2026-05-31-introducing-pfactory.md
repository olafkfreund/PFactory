---
layout: post
title: "Introducing PFactory: test quality, not test count"
subtitle: "Why we grade generated tests on five signals instead of dumping fifty and calling it coverage."
date: 2026-05-31
author: DataSeek Team
---

Most AI test generators optimise for the wrong number. They read your code,
emit a pile of tests, and report a coverage percentage. But coverage measures
which lines *ran* — not whether a single assertion would ever catch a
regression. A suite can be 95% green and verify nothing.

PFactory starts from a different premise: **hand off a finished feature, get
back a small set of tests you can actually trust — graded, ranked, and tied to
your acceptance criteria.**

## The pipeline

You give PFactory a finished feature on a branch (from AIFactory, Claude Code,
or any tool — via the MCP control plane or a markdown / Gherkin / EARS file).
Five agents take it from there:

```
Planner → Gen-Functional → Executor → Evaluator → Triager
```

The Planner reads your acceptance criteria and emits a lane-tagged plan.
Gen-Functional writes tests per subtask. The Executor runs them in a locked-down
Docker sandbox. The Evaluator scores each test. The Triager dedups, ranks, and
hands you a triage report — the good tests to merge, the bad run to dismiss.

## The 5-signal verdict

Every generated test gets a verdict — **accept**, **flag**, or **reject** —
from five signals, not one:

1. **Coverage delta** — what this test newly exercises.
2. **3× stability** — does it pass deterministically, or is it flaky?
3. **Mutation** — mutate the code; does the test *catch* it? KILLED vs SURVIVED.
4. **Lint promotion** — static flake-risk patterns promoted to warnings.
5. **Semantic relevance** — does the assertion actually verify the criterion?

A test that runs but survives every mutation is coverage theatre — PFactory
flags it. A test that's stable, kills its mutant, and maps cleanly to an AC gets
accepted. You review verdicts, not raw output.

## Polyglot by design

v0.2 ships a five-lane spine — **unit · browser · api · integration ·
mutation** — across pytest, Jest, and Playwright. One spec can fan out across
languages; the verdict pipeline scores them uniformly.

## What's next

We're posting here as we go — design decisions, the things that surprised us,
and where the moat is. If you care about test *quality* over test *count*,
[the architecture page](/architecture/) is a good next stop, and the
[demos](/demos/) show the whole handover end to end.
