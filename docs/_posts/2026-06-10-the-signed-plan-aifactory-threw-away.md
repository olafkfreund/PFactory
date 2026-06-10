---
layout: post
title: "The signed plan AIFactory quietly threw away"
subtitle: "A 422 on the handoff made PFactory fall back to re-planning — silently. v0.6.13 fixes the trusted-plan path so the governed contract survives the boundary."
date: 2026-06-10
author: DataSeek Team
---

The worst bugs aren't the ones that crash. They're the ones that succeed at the
wrong thing. Today's work — shipped across v0.6.8 through v0.6.13 — was almost
entirely chasing one of those: a handoff that returned green while throwing away
the entire point of PFactory.

## What's meant to happen

PFactory sits between an AI plan and execution. It ingests a plan, enriches it
with read-only live-cloud context, runs the feasibility and review gates, takes
one human approval, and emits a **signed Task Contract** — the governed,
tagged plan an execution agent builds from. In PARR, that contract is handed to
AIFactory on the *trusted-plan fast path*: skip planning, you already have a
sanctioned plan, go straight to code.

```
PFactory  ──(signed contract)──►  AIFactory  ──►  TFactory
   plan + gates + approval          build          verify
```

The handoff is the whole moat. If the contract doesn't cross intact, the
approval, the feasibility gates, and the bundled TFactory test plan all
evaporate.

## What was actually happening

Two failures, stacked.

First, **a 401** (v0.6.11). The contract emit POSTed to AIFactory's
`/api/tasks/from-plan` with no `Authorization` header, so the auth middleware
rejected it outright and `emit-contract` died with a 500. We now attach a
`Bearer` token from the shared in-cluster secret.

Then, with auth fixed, **a 422** — and this is the quiet one (v0.6.13).
PFactory posted the bare contract as the request body. But AIFactory's
`FromPlanRequest` expects the signed contract nested under a `plan` field, with
`title` and `description` as query params. Shape mismatch → 422.

Here's the part that matters: PFactory didn't surface the 422. It **silently
fell back to `create-and-run`**. AIFactory dutifully accepted the task — and
re-planned it from scratch. The signed contract, the human approval, the
TFactory test plan tagged into it: all discarded. The pipeline went green. The
governed plan was gone.

## The fix

Three lines of substance, one of which is a posture change:

1. **Shape the payload right.** Post `{plan: contract}` with `title`/`description`
   as query params, the way `FromPlanRequest` actually reads it.
2. **Share the key.** `AIFACTORY_TRUSTED_PLAN_KEY_PFACTORY` is now set on both
   sides so the fast path is authorised, not just authenticated.
3. **Make file footprints optional** on AIFactory's side, so a valid contract
   isn't rejected for a field the plan stage legitimately leaves open.

A silent fallback is worse than a loud failure. The fallback existed for
resilience, but resilience that discards the signed plan isn't resilience — it's
a governance hole that reads as success on the dashboard.

## The supporting cast

The same day cleared the rest of the path so the contract has somewhere sane to
start from:

- **GitHub issue → plan, not a coding task** (v0.6.9, v0.6.12). Inherited from
  the AIFactory fork, the issue entry points created a *build* spec. They now
  ingest into the planning pipeline with the correct `github_issue` channel, so
  an issue lands in **Plans ready** on the board.
- **"View Plan" opens the plan** (v0.6.10), not just the Planning view.
- **Bash sandbox toggle for k3d** (v0.6.8). bwrap can't mount `/proc` when the
  node is itself a container, so the SDK bash sandbox broke every agent command.
  Gated behind `AIFACTORY_BASH_SANDBOX`; isolation falls back to the pod
  boundary and the command allowlist.

All verified end-to-end on the live cluster, not just in unit tests.

## Why this is the work

PFactory's pitch is that the plan is governed — that what gets approved is what
gets built. A handoff that quietly substitutes a re-plan breaks that promise
without telling anyone. Closing it is less glamorous than a new feature, and
more important than most.

If you want the shape of the contract that now crosses the boundary intact, the
[architecture](/architecture/) page walks the full plan-stage pipeline.
