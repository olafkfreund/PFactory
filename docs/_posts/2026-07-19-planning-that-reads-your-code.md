---
layout: post
title: "Planning that reads your code"
subtitle: "PFactory does not guess at a plan from a prompt. It clones the target repo, reads it, and signs a contract about that specific codebase — then hands it to a factory that builds, tests, and grades the result unattended."
date: 2026-07-19 12:00:00
author: DataSeek Team
---

Most planning tools take a prompt and produce a plausible-sounding plan. The plan
sounds right because it is generic. It has never seen your repository. PFactory
starts from the opposite premise: a plan is only worth signing if it is grounded
in the code that already exists. So before it writes a single line of plan, it
reads the code.

## The plan is built by reading the repository

PFactory is the Plan stage of the Factory pipeline, four cooperating services
that take a GitHub issue in and produce a tested pull request out. When a task
arrives, PFactory clones the target repository read-only and builds a RepoMap of
it: the languages present, the frameworks and package managers in use, the
infrastructure-as-code, and the exact commit it read. That map is the ground
truth the rest of planning reasons against.

From there it does the work a careful engineer would do before proposing a change:

- **Classifies the change.** For this cycle's runs the classifier set
  `change_mode = modify` — this is an edit to existing code, not a greenfield
  build, and the plan is shaped accordingly.
- **Grounds the plan in delivery history.** A DORA pass reads how this codebase
  has actually been delivered, so the plan reflects the real cadence and risk of
  the repository rather than an abstract ideal.
- **Enriches from house standards and Backstage.** The planner pulls the live
  Backstage catalog and best-practice knowledge during an enrichment pass, so the
  plan inherits the conventions the organisation already committed to.
- **Scans for injection.** An injection scan runs over the inputs before anything
  is trusted.
- **Scores review lenses.** Feasibility and architecture lenses graded the plan
  1.0 and 1.0 this cycle. The plan reviews itself before a human ever sees it.

The output is not a suggestion. It is a signed Task Contract with explicit
acceptance criteria — a contract about this codebase, at this commit, not a guess
extrapolated from a sentence.

## Then the rest of the factory earns the plan

A plan is only as good as what happens to it. PFactory hands its contract
downstream, and the contract is what keeps the downstream services honest.

AIFactory builds each task inside its own throwaway Kubernetes Job, refreshed to
the current main tip, then opens its own pull request; the Job is then gone. A
tamper-evident test-evidence gate makes a green test checkbox impossible unless a
real test runner actually executed.

TFactory then generates tests, runs them in a per-task sandbox, and grades the
result on five signals — coverage, stability, mutation, semantic relevance, and
CI parity — before assigning a Verification Assurance Level recomputed from the
truth. A failing lower lane caps the ceiling; an untested dimension is reported as
an honest gap, never a silent pass. The verdict threads back onto the pull request
the factory opened.

## Tests that refuse to lie

The strongest thing this cycle demonstrated was a refusal. Minutes before a clean
run, a `slugify` helper built and looked fine — but failed one of twelve test
verdicts on a unicode edge case. The verification gate capped it at VAL-0 and
auto-filed a handback to fix it. It would not certify a build with a failing test.
That is the capability, not a bug.

The clean run that followed — a `clamp(value, low, high)` helper — was verified to
VAL-1: five of five acceptance criteria met, nine tests generated and kept, none
rejected, the mutation probe killed, confidence 0.96, stable across three runs.
VAL-2 and VAL-3 were correctly reported as not run, because no API, integration,
or browser lane applies to a pure function. The gate claims exactly what it
verified and nothing more.

One honest caveat from the same run: the verify verdict is computed correctly, but
its automatic post back to the pull request is gated by a fix we are now tracking
as an issue. The factory found the last rough edge in its own feature and said so,
rather than hiding it.

## Why separate planning at all

The industry mostly collapsed planning and execution into one loop, so the plan
became whatever the agent happened to do. Pulling planning back out gives you a
place to put a governance gate, an acceptance criterion, and a plan that is
accountable to the real repository. When planning reads your code first, the
contract it signs is about your system — and everything the factory does after is
measured against it.

A live walkthrough of all four portals is available on request.
