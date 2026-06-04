# PFactory → AIFactory / TFactory tag taxonomy

> Status: v1 (2026-06-04) · Owner: PFactory · Consumers: AIFactory, TFactory

This is the **contract** PFactory uses when it emits governed GitHub epics + child
issues on plan approval. The labels are the "secret language" that lets AIFactory
(and TFactory) recognise, classify, and pick up work that PFactory has reviewed
and a human has approved. **PFactory writes these labels; AIFactory/TFactory read
them.** Nothing is picked up automatically — emission only happens after the AI
gates pass *and* a human approves (dual approval).

The taxonomy deliberately **reuses AIFactory's existing labels** (`epic`, `sev:*`,
`backend`, `frontend`, `mcp`, `security`) and adds only the PFactory-specific ones.

---

## 1. Mandatory marker

| Label | Meaning |
|-------|---------|
| `pfactory` | **Created by PFactory.** Present on every epic and child issue PFactory emits. AIFactory/TFactory use this to know the issue is governed (reviewed + human-approved) rather than hand-filed. |

## 2. Routing labels

| Label | Meaning |
|-------|---------|
| `handoff:aifactory` | Route this issue to **AIFactory** for execution (plan → code → QA). |
| `handoff:tfactory` | Route this issue to **TFactory** for test generation + execution. Carried by `type:testing` children and by any child whose acceptance criteria need an independent test pass. |

A child may carry **both** handoff labels (build it, then test it).

## 3. Classification labels (PFactory-specific — to be created)

| Label | Allowed values | Meaning |
|-------|----------------|---------|
| `type:<x>` | `software` · `feature` · `infra` · `hosting` · `testing` · `cicd` · `product` | The category of work. Derived from the plan's detected category. |
| `plan-type:<x>` | `software-service` · `data-pipeline` · `infra-change` · `generic-deliverable` (extensible) | The PFactory plan-type descriptor that gated which stages ran. |
| `priority:<p>` | `p0` · `p1` · `p2` · `p3` | Execution priority. `p0` = blocking/critical path. |

## 4. Reused AIFactory labels

| Label | Values | Source |
|-------|--------|--------|
| `epic` | (marker) | existing — applied to the parent epic issue |
| `sev:<x>` | `critical` · `high` · `medium` · `low` | existing — severity of the most severe **blocking/high** review finding attached to the issue |
| `area:<x>` / `backend` / `frontend` / `mcp` / `security` | existing | existing — applied when the child clearly maps to one |

> PFactory uses AIFactory's `sev:*` for severity rather than inventing a parallel
> scale. A plan-level **risk** signal still rides in the metadata block (§5).

## 5. Metadata block (issue body + `requirements.json`)

Labels classify; the **body** carries the governed detail AIFactory/TFactory parse.
Every PFactory-emitted issue ends with a machine-readable block:

```yaml
<!-- pfactory:meta
plan_id: 001-orders-platform
plan_type: infra-change
category: infra
priority: p1
risk: medium
cost_monthly_usd: 2492.58          # feasibility cost estimate (confidence: medium)
effort_points: 39                  # feasibility effort rollup
effort_days: [15.6, 39.0]
access_verified: true              # IAM policy-simulation passed for required actions
citations:                          # every requested change cites a source
  - why: "A networked service needs auth."
    uri: "https://owasp.org/..."
    source: "owasp"
-->
```

The same fields are written to `.aifactory/specs/<plan_id>/requirements.json`
(`metadata` object) when PFactory hands off via the file path, so AIFactory gets
full fidelity even without parsing the issue body.

## 6. How consumers react

**AIFactory** (`handoff:aifactory`):
1. Treat `pfactory`-labelled issues as governed specs — skip its own planning gate
   for them (PFactory already reviewed + a human approved).
2. Parse the `pfactory:meta` block (or `requirements.json`) → seed the spec's
   `complexity`, `priority`, and carry `cost`/`effort`/`access`/`citations` into
   the planner context.
3. Map `priority:p0..p3` → its scheduling, `type:*` → its track selection.

**TFactory** (`handoff:tfactory`, `type:testing`):
1. Pick up testing children as test-generation targets.
2. Use the acceptance criteria + `citations` as the test oracle.
3. **Label differences:** TFactory has no `sev:*` and uses a horizon-based
   priority scheme (`priority:now|next|later`) rather than `p0–p3`. TFactory maps
   the PFactory `priority:p*` from the `pfactory:meta` block onto its horizons
   (`p0→now`, `p1→next`, `p2/p3→later`) or consumes `priority` from the metadata
   directly. It still needs the new `pfactory`, `handoff:tfactory`, `type:testing`
   labels created; it reuses its existing `epic`/`task`/`backend`.

## 7. Example

Epic: `pfactory`, `epic`, `handoff:aifactory`, `type:infra`, `plan-type:infra-change`, `priority:p1`
Child (build): `pfactory`, `handoff:aifactory`, `type:infra`, `priority:p1`, `sev:high`
Child (tests): `pfactory`, `handoff:tfactory`, `type:testing`, `priority:p2`

## 8. Versioning

This taxonomy is versioned at the top of this file. PFactory emits the version in
the `pfactory:meta` block (`taxonomy: v1`) so consumers can branch on it. Additive
changes (new `type:*`/`plan-type:*` values) are minor; renames/removals are major
and coordinated with AIFactory + TFactory.
