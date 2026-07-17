---
layout: post
title: "The issue becomes the plan"
subtitle: "A GitHub issue is now a first-class intake channel: the from-issue endpoint is live in production, dev and main are reconciled, and plan sessions learned to carry a tenant."
date: 2026-07-17 12:00:00 +0000
author: DataSeek Team
---

PFactory is the Plan stage of the Factory pipeline. This cycle the front door
got wider: a labeled GitHub issue can now walk straight into planning without a
human pasting its contents into the portal first.

## From issue to plan session, in production

`POST /api/plan/sessions/from-issue` is live in production. When AIFactory's
intake poller sees a `factory:hard` issue, it forwards the issue to PFactory
(AIFactory#874), and PFactory turns it into a real plan session: the issue body
becomes the plan text, the session records the origin issue number at intake,
and the correlation key back to the filed issue is resolved the moment the
session exists — not later at emit. A session that never reaches emission still
knows exactly which issue it came from.

The payload is deliberately the poller's own contract — `repo`, `provider`,
`issue_number`, `title`, `body`, `labels`, `autonomy_tier`, `change_mode` — so
the field names cannot drift between the two services.

There is one rule at the door: the issue must carry acceptance criteria. A
heading such as `## Acceptance Criteria` (or `Acceptance` / `Requirements`)
followed by bullets, or inline `AC#N:` lines, is enough. An issue without them
gets an actionable 400 that names the fix — better a clear rejection than a
plan session built from a one-line title nobody can verify against.

## Dev and main, reconciled

For a while the `dev` branch had been accumulating fixes that never reached
production — roughly nineteen thousand lines of divergence between `dev` and
`main`. That gap is now closed. The branches were reconciled in both
directions, the ratchet violations the merge surfaced were cleared, and the
stranded fixes — including the atomic secret-file writes and the jobstore
lease-and-reclaim work — are shipped and running in production.

The lesson is the boring one: a fix that lives only on `dev` is not shipped,
and an issue it "closes" is not closed. The fleet now has CI that auto-closes
issues only when the fix actually reaches `main`.

## Plan sessions learn tenancy

Behind the `PFACTORY_MULTI_TENANT` flag (#308), plan sessions are now
tenant-scoped. Off by default, nothing changes: every session belongs to the
single `default` tenant and local single-user behaviour is untouched. Switched
on for a hosted deploy, the tenant is resolved per request from the
`X-Tenant-Id` header the ingress stamps from the identity provider's tenant
claim, session listings filter to the caller's tenant, and the durable
`job_states` store gained a `tenant_id` column via migration.

The tenant also travels downstream: a non-default tenant is stamped into the
emitted Task Contract as `provenance.tenant_id`, so AIFactory can keep the
whole PARR chain tenant-scoped. Default-tenant contracts stay byte-identical
to what they were before — the flag costs nothing until it is used.

## Small honesty: torn writes in tests

A flaky test turned out to be a real pattern problem: hand-rolled JSON writers
in the test suite could produce torn files under concurrency. They now route
through the same `atomic_write_secret_json` helper production code uses
(#314) — write to a temp file, then rename. One code path for writing secrets,
in tests and in production alike.

## What is next

Exercise the from-issue door at volume — the hard tier is only as autonomous as
its intake — and take multi-tenancy from flag to default on the hosted deploy.
