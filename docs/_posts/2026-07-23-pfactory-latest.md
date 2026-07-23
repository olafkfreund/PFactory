---
layout: post
title: "What shipped in PFactory: code-aware plans, hardened boundaries"
subtitle: "A round-up of the recent work — reconnaissance that reads the target repo before it plans, SpecKit constitutions the readiness gate enforces, deployment-aware plans, and a path-injection barrier that shuts a class of traversal bugs at the choke points."
date: 2026-07-23 12:00:00 +0000
author: Olaf Freund
---

PFactory is the planning stage of the Factory line: it ingests a request, grounds
it in your real infrastructure, decomposes it into governed work, and emits a
signed Task Contract that AIFactory builds and TFactory verifies. The last few
weeks were about two things — making the plan *know* the codebase it is changing,
and hardening the boundaries where untrusted input crosses into the planner.

## Plans that read the code first

The biggest shift is that PFactory no longer plans from the prompt alone. A
read-only reconnaissance stage (`plan/recon/`) runs before decomposition. It does
a static, single-branch, blobless checkout of the target repository — git hooks
disabled, size and time capped, always torn down, and it **never executes repo
code** — then builds a `RepoMap`: the languages present, the frameworks and
package managers in use, and a static Terraform / Helm / Kubernetes inventory.

That map changes the plan in concrete ways. Acceptance criteria and child issues
are mapped to **real files**, so subtasks leave the planner with populated
`files_to_modify` and `files_to_create` instead of guesses. And the plan's
implementation language now comes from the repo, not from keywords in the plan
text. That closes the wrong-language trap directly: a new `language-reconciled`
readiness check **halts** a non-migration plan whose spec language disagrees with
the repo it was read from, rather than quietly emitting the repo's language and
hoping. Directional rewrites ("port this service from Python to Rust") are
recognised as migrations; PFactory extracts the behavioral contract by AST and
carries an equivalence lane forward for TFactory to grade against.

## Constitutions the gate actually enforces

PFactory now ingests a [SpecKit](https://github.com/github/spec-kit) `.specify`
spec and its project constitution. This is not decoration: a `constitution-grounded`
readiness check holds the plan to the constitution's articles, and an approved
plan can emit the constitution and spec back out as governed documentation next to
the epic. Deployment-bearing plans get the same treatment — a
`deployment-pipeline-present` check refuses to pass a plan that ships a deployment
but carries no pipeline definition, and a deployment feasibility assessment rides
into the contract.

These join the existing hard readiness gate. A plan cannot be emitted until the
checks pass or a human records a waiver bound to the plan's content hash — so the
work that ships is provably the work that was approved.

## Hardening the boundaries

The planner reads untrusted specs, clones repositories it did not write, and
accepts caller-supplied ids over its API. Recent work tightened every one of those
seams:

- **Path-injection barrier (#335).** Caller-supplied spec and task ids are
  validated and barriered before they can ever compose a filesystem path, and the
  file-browser and project endpoints are confined to the workspace root. The fix
  lives at the shared choke points every route flows through, not bolted onto each
  caller — so a traversal attempt is rejected once, everywhere.
- **A shell allowlist that reads shell.** Agent tool commands are parsed with a
  real `bashlex` AST walker that surfaces every nested command inside `$(...)`,
  backticks, and pipes to the allowlist. `PFACTORY_STRICT_COMMAND_PARSING=1` fails
  closed on input it cannot parse.
- **Fail-closed exposure surface.** `/mcp` returns 401 when `PFACTORY_MCP_SECRET`
  is unset outside dev, credentialed CORS rejects wildcard origins, and the server
  refuses to boot with `DISABLE_AUTH` set on a non-loopback host. CodeQL code
  scanning now runs in CI, and API error responses no longer leak stack traces.

## Documenting the surface

Alongside the code, every environment variable the service reads — backend and web
server, defaults, and whether each one is security- or write-affecting — now lives
in one exhaustive reference at
[`docs/dev/environment-reference.md`](/environment-reference/), with a completeness
ledger that reconciles the doc against a grep of the source. If the planner reads a
variable, it is written down.

The theme through all of it: a plan is only worth signing if it is grounded in the
code, checked against your standards, and produced behind boundaries you can trust.
