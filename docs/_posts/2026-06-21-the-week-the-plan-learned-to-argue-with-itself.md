---
layout: post
title: "The week the plan learned to argue with itself"
subtitle: "A red-team lens that attacks the spec before a human signs it, deployment-aware planning that derives where the work runs, cost-aware model routing, and the concurrency groundwork that lets PFactory plan many things at once. Week of 2026-06-15 to 2026-06-21."
date: 2026-06-21 12:00:00 +0000
author: Olaf Freund
---

PFactory's job is to make the plan the artifact you review — governed, grounded,
and auditable before anything gets built. This week pushed on all three of those
words at once. The plan now gets attacked by an adversarial lens before a human
ever sees it; it derives where it will run, not just what to build; it routes each
phase to a model chosen on cost as well as capability; and the control plane took
the steps it needs to plan more than one thing at a time. Here is the honest
state of each.

## The plan now argues with itself: the Red Team lens (RFC-0015)

The review gates have always read a spec the way a reviewer would — architecture,
security, best-practice, feasibility. The trouble with a friendly reviewer is that
it reads the spec the way the author meant it. An attacker does not.

[RFC-0015](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0015-speckit-informed-planning.md)
landed an **adversarial "Red Team" spec-review lens** (PFactory #216) that does the
opposite of confirming the plan. It looks for the missing failure mode, the
unstated assumption, the acceptance criterion that is satisfiable by a broken
implementation, the security boundary the happy-path description quietly steps
over. It runs as a review lens, so its findings surface in the same place as the
other gates — before the one human approval, not after the build has already
spent tokens on the wrong thing.

Alongside it, the same RFC made PFactory **bilingual with spec-kit**:

- **Per-project constitution injection with a hard check (#213).** A project can
  declare a constitution — its non-negotiable rules — and PFactory injects it into
  planning *and* hard-checks the emitted plan against it. A plan that violates the
  constitution is held, not shipped.
- **Spec-kit artifact ingest (#214).** PFactory can read `.specify` spec/plan/tasks
  artifacts as an input, so a team already living in spec-kit can hand PFactory
  their existing documents instead of starting over.
- **Spec / plan / tasks Markdown emit (#215).** And it emits the same shape back
  out, so the governed plan is portable rather than locked inside PFactory.

The throughline: interoperability without giving up the gate. You can bring your
own spec format and your own constitution, and the red-team lens still gets its
swing before sign-off.

## Plans that know where they run: deployment-aware planning (RFC-0013)

A plan that says *what* to build but not *where it runs* hands the gap to whoever
builds it. This week PFactory closed that gap on the planning side.

[RFC-0013](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0013-deployment-aware-planning.md)
added a **deployment-discovery-and-derivation stage** (#188–#190): PFactory reads
the target's deployment reality — the Terraform / Helm / Kubernetes inventory the
code-aware recon stage already surfaces — and **derives a `deployment` block** on
the Task Contract describing how and where the work is expected to run. The
vendored Task Contract schema gained the matching `$defs.deployment` definition,
synced from the canonical Factory hub copy and guarded by the cross-repo
schema-drift CI step. There is an **end-to-end proof** for it (#154) so the stage
is exercised, not just declared.

Honest scope: this is the *planning* half. The plan now carries a grounded
deployment block, and that block is what a downstream builder and tester need to
target the right environment. The end-to-end *deploy-then-verify* loop — actually
standing the deployment up and checking it — runs as a dry-run lane in the wider
program and is not yet a live, environment-touching gate. The contract is ready
for it; the live verification is the next step, not a shipped one.

## Cost-aware model routing (RFC-0014)

Picking the model per phase used to be a capability decision: hard phases got the
strong model, simple phases got the fast one. This week added **cost** to that
decision.

[RFC-0014](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0014-cost-aware-model-routing.md)
shipped **cost-aware model routing with effort-free scoring** (#208–#211). Each
phase is routed to a model chosen on a score that weighs capability against price,
rather than a hardcoded per-complexity default. The Settings dialog's agent
profiles still let you pin a model per phase by hand when you want determinism;
the routing is the new default when you do not.

## Planning more than one thing at once (RFC-0016)

Everything above is moot if PFactory can only plan one project at a time. This
week laid the durable foundation for concurrency.

[RFC-0016](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0016-concurrency-and-scaling.md)
moved PFactory off single-instance assumptions:

- **Durable Postgres job-state with multi-replica admission (#217).** Plan job
  state lives in Postgres rather than in one process's memory, and an admission
  cap lets multiple replicas coordinate instead of trampling each other.
- **`process()` offloaded off the event loop (#217).** The heavy planning work no
  longer blocks the async event loop, so a long-running plan does not freeze the
  API for everyone else.
- **Pooled-worker as the explicit Phase-2 execution model (#218).** The RFC now
  names the pooled-worker design as the path forward, rather than leaving it
  implicit.
- **Plan documents emitted to object storage (#190, RFC-0016).** Emitted plan
  artifacts go to object storage, so they are not tied to a single replica's
  local disk — a prerequisite for replicas that come and go.

The mechanisms are in. The live multi-replica rollout is being flipped carefully
rather than all at once, and not every default is on yet — the durable state and
object-store emit are the load-bearing parts that had to land first.

## The foundation under all of it: code-aware planning (RFC-0010)

The deployment block, the red-team lens, and the grounded acceptance criteria all
stand on the **code-aware planning** work that shipped in 0.7.0
([RFC-0010](https://github.com/olafkfreund/Factory/blob/main/docs/rfc/0010-code-aware-planning-and-behavioral-equivalence.md)).
PFactory reads the target repository — a static, read-only, never-executed
checkout — builds a `RepoMap`, classifies the change as greenfield / modify /
migration, and reconciles the planning language against what the repo actually
uses instead of guessing from plan text. That is why a "needs attention" plan on
the board can point at real files, and why the deployment stage has an inventory
to derive from. You can see the resulting index in the new
[portal tour]({{ '/gallery/' | relative_url }}) under **Index &amp; Memory**.

## What is in progress

- **Live deploy-then-verify.** The deployment block is on the contract; standing
  the deployment up and verifying it end-to-end is the next increment, not a
  shipped gate.
- **Multi-replica rollout.** The durable state and object-store emit are in; the
  live concurrent rollout is being flipped incrementally.
- **Constitution and spec-kit adoption.** The ingest, emit, hard-check and
  red-team lens are shipped; turning them on for real projects is the rollout
  ahead.

## See it

The portal gallery was refreshed this week, captured from the live
[portal](https://pfactory.freundcloud.org.uk/): the board with its needs-attention
and verified plans, the ingest and task dialogs, the MCP and skills registries,
the code-aware index, and the settings where the agent profiles and cost routing
live. Take the [tour]({{ '/gallery/' | relative_url }}).
