---
layout: post
title: "What shipped in PFactory, August to November 2026"
subtitle: "Several replicas on a shared store, org-scoped access control (mostly in shadow mode), seven review lenses running today, audit evidence, and a round of security fixes. Some of it is off by default, and the post says which."
date: 2026-11-15 09:00:00 +0000
author: DataSeek Team
---

Since the [last post](/blog/2026/08/10/a-plan-that-argues-with-itself/),
PFactory has had [26 releases](https://github.com/olafkfreund/PFactory/releases),
from [v0.6.17](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.17)
(2026-09-03) to [v0.6.42](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.42)
(2026-10-31). Nothing was tagged between 10 August and 2 September, and nothing
has been tagged since 31 October (see the [release list](https://github.com/olafkfreund/PFactory/releases)). Some items in
[v0.6.19](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.19) were
merged earlier than the tag date, because the notes call them previously
unversioned on `main`.

This post covers what is released. Work that is only on `dev` is at the end,
under "In progress".

## Running as several replicas on a shared store

Plan sessions are shared across replicas through Postgres, with atomic id
allocation, one emit per session (a second concurrent emit gets a 409) and
compare-and-set writes
([v0.6.20](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.20),
[#755](https://github.com/olafkfreund/PFactory/issues/755),
[#771](https://github.com/olafkfreund/PFactory/pull/771)). Running tasks,
insights replies, changelogs and PR reviews are tracked in a shared
`run_leases` table, so a duplicate start gets a 409 and a stop reaches the
replica that owns the run
([v0.6.25](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.25),
[#805](https://github.com/olafkfreund/PFactory/issues/805)).

The rest of the shared state followed:

- WebSocket events are relayed between pods through Postgres
  ([v0.6.30](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.30)).
- Task logs and board status live in Postgres, and task logs are pruned after
  `PFACTORY_TASK_LOG_RETENTION_DAYS` (default 90)
  ([v0.6.31](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.31)).
- `GET /api/drain` reports whether a pod holds a run, and the Helm chart ships
  a preStop drain behind `drain.enabled`
  ([v0.6.34](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.34)).
- Postgres connection pools are capped per pod, and boot migrations take an
  advisory lock so pods that start together apply each revision once
  ([v0.6.40](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.40),
  [v0.6.42](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.42)).

This does not mean autoscaled multi-replica production. The
[v0.6.20 notes](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.20)
say the one-replica pin on the autoscaler stays in place, and lifting it is a
separate step.

## Org-scoped access control, mostly in shadow mode

- Plan session reads are scoped to the caller's org. This is enforced for
  org-scoped `acw_` API keys and runs in shadow mode for human viewers
  ([v0.6.41](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.41),
  [#976](https://github.com/olafkfreund/PFactory/pull/976)).
- Plan session writes return a 404 for another org's session, with the same
  split between keys and humans
  ([v0.6.42](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.42),
  [#985](https://github.com/olafkfreund/PFactory/pull/985)).
- Org role checks on 39 task and plan-pipeline write routes run in **shadow
  mode**: a would-be denial is logged and audited, and responses do not change
  until `RBAC_ENFORCE_TASK_ROUTES` (off by default) is set
  ([v0.6.38](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.38),
  [#950](https://github.com/olafkfreund/PFactory/pull/950); extended to more
  routes in [v0.6.39](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.39),
  [#957](https://github.com/olafkfreund/PFactory/pull/957)). A runbook for
  turning enforcement on came in
  [v0.6.41](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.41)
  ([#972](https://github.com/olafkfreund/PFactory/pull/972)).
- `acw_` key scopes are capped by the owner's current org role, so a removed or
  demoted owner loses access through their keys
  ([v0.6.37](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.37),
  [#938](https://github.com/olafkfreund/PFactory/pull/938)). `GET /api/audit/export`
  now needs org admin
  ([#942](https://github.com/olafkfreund/PFactory/pull/942)).
- Remote MCP checks the calling key on each tool call. It is off unless
  `PFACTORY_MCP_REMOTE_ENABLED` is set
  ([v0.6.40](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.40),
  [#966](https://github.com/olafkfreund/PFactory/pull/966)).
- Worktree endpoints now need `project_id:spec_id`, and a bare spec id gets a
  400 ([#974](https://github.com/olafkfreund/PFactory/pull/974),
  [v0.6.41](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.41)).

## Seven review lenses

Six lenses always run: feasibility, architecture, security, compliance,
best-practices and completeness. A seventh, red-team, is added when the
extension registry enables it, and it is enabled by default. The red-team lens
is not new in this window: it shipped in June
([RFC-0015](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0015-spec-driven-interop-and-validated-gap-features.md),
[#216](https://github.com/olafkfreund/PFactory/issues/216)). The compliance
lens is marked mandatory in the registry and cannot be switched off there
([v0.6.20](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.20),
[#682](https://github.com/olafkfreund/PFactory/issues/682)). Earlier copy on this
site and in the README said four, which was out of date.

Other review changes:

- A plan whose worked example contradicts its own invariant is blocked
  ([v0.6.19](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.19),
  [#402](https://github.com/olafkfreund/PFactory/issues/402)), and the per-lens
  verdict is published so the cockpit can name the lens that blocked a plan
  ([CFactory#245](https://github.com/olafkfreund/CFactory/issues/245)).
- Plan-session routes reject unknown body fields with a 422, which is a
  behaviour change
  ([v0.6.20](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.20),
  [#671](https://github.com/olafkfreund/PFactory/issues/671)).
- Code-aware planning reads C#, Kotlin, PHP, Swift and C/C++ paths
  ([v0.6.19](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.19),
  [#510](https://github.com/olafkfreund/PFactory/issues/510)).
- Cost routing honours a contract's pinned model and declared class, and skips
  unavailable models
  ([v0.6.30](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.30),
  [#830](https://github.com/olafkfreund/PFactory/issues/830)).

## Governance and audit evidence

- The run signature (`<slug>.run.json`, `signature_schema: 2`) records risk
  class, autonomy tier, data classification, approver and the control domains
  the run engaged. Unknown values are `"unknown"` or `null`. At this release
  `change_id`, `originator`, `agent_identity` and `model_route` were still
  `null`
  ([v0.6.27](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.27),
  [Factory#1961](https://github.com/olafkfreund/Factory/issues/1961)).
- `python -m plan.governance_cli` answers which plans engaged a control, at what
  tier and approved by whom, and prints how many rows it scanned
  ([v0.6.27](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.27)).
- Concurrent audit writes no longer fork the hash chain, and old chains still
  verify
  ([v0.6.23](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.23),
  [#806](https://github.com/olafkfreund/PFactory/issues/806)).
- A completion event can claim `emitted` only with evidence, such as an epic
  issue. Otherwise it is reported `failed`
  ([v0.6.18](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.18),
  [#141](https://github.com/olafkfreund/PFactory/pull/141)).
- The emitted contract can carry an Ed25519 signature next to the HMAC one. It
  is warn-only and no key is configured, so nothing changes yet
  ([v0.6.35](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.35),
  [#929](https://github.com/olafkfreund/PFactory/pull/929)).

## Security hardening

- The command allowlist parses shell grammar (bashlex), so a command nested in
  `$(...)`, backticks or a pipe is checked. `PFACTORY_STRICT_COMMAND_PARSING=1`
  fails closed
  ([v0.6.17](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.17),
  [#133](https://github.com/olafkfreund/PFactory/pull/133)).
- `/mcp` returns 401 when `PFACTORY_MCP_SECRET` is unset outside dev, CORS
  rejects wildcard origins with credentials, and the server refuses to boot
  with `DISABLE_AUTH` on a non-loopback host
  ([v0.6.17](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.17),
  [#131](https://github.com/olafkfreund/PFactory/pull/131)).
- OIDC ID tokens are checked against PFactory's own config (issuer, audience,
  `exp`, asymmetric signature only). Azure AD v1 issuers must move to v2.0
  ([v0.6.39](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.39),
  [#956](https://github.com/olafkfreund/PFactory/pull/956)).
- Dependency and image CVEs that failed the Trivy P0 gate were cleared, most
  recently uv CVE-2026-111943
  ([v0.6.42](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.42),
  [#983](https://github.com/olafkfreund/PFactory/pull/983)).

## Fitting the rest of the line

- The handback sender to AIFactory sends a valid `handback-triage-1` block with
  bearer auth. Sending is **off by default** (`PFACTORY_HANDBACK_SEND`)
  ([v0.6.35](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.35),
  [#928](https://github.com/olafkfreund/PFactory/issues/928)).
- The completion webhook to CFactory authenticates with a bearer token, and
  CFactory accepts events with or without it until its own enforce step
  ([v0.6.35](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.35),
  [#931](https://github.com/olafkfreund/PFactory/pull/931)).
- Correlation keys are scoped to the repo, so issue 12 in two repos no longer
  lands on one CFactory work item
  ([v0.6.40](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.40),
  [#965](https://github.com/olafkfreund/PFactory/pull/965)).
- Runtime invariants are on by default: each package declares checks in
  `_invariants.py`, and violations are logged and counted. No check is critical
  yet, so the readiness probe is unchanged. `FACTORY_INVARIANTS=0` turns them
  off
  ([v0.6.28](https://github.com/olafkfreund/PFactory/releases/tag/v0.6.28),
  [#864](https://github.com/olafkfreund/PFactory/issues/864)).

## In progress

These are merged to `dev` and are in no release yet:

- Scoped `acw_` keys on WebSockets
  ([#991](https://github.com/olafkfreund/PFactory/pull/991)), and `/ws/events`
  confined to the orgs the caller may read. JWT sockets stay unfiltered until
  `RBAC_ENFORCE_TASK_ROUTES` is on
  ([#994](https://github.com/olafkfreund/PFactory/pull/994)).
- An allowlisted environment for agent child processes, so they cannot read the
  control plane's tokens, database URL or S3 keys
  ([#997](https://github.com/olafkfreund/PFactory/pull/997)).
- Originator and agent identity recorded at intake and emitted on the run
  signature
  ([#1000](https://github.com/olafkfreund/PFactory/pull/1000)).
- A Helm hook Job that runs `alembic upgrade head` when
  `migrations.autoApply=false`
  ([#987](https://github.com/olafkfreund/PFactory/pull/987)).

The full list is on the [release page](https://github.com/olafkfreund/PFactory/releases).
