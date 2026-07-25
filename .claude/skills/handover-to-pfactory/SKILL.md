---
name: handover-to-pfactory
description: Hand a plan or request to PFactory for governed planning. Ingests it (markdown / Gherkin / EARS / pdf / docx, inline or a file), runs the pipeline (enrich with live cloud + Backstage context, decompose into an epic + child issues, feasibility, the review lenses, the hard readiness gate), and returns a cited review a human can approve. Nothing reaches GitHub or AIFactory until the gates pass and a human approves.
when_to_use: When the user has a plan, spec, or feature request and wants PFactory to review and govern it before any code is written. Common triggers — "hand this to pfactory", "/handover-to-pfactory", "plan this properly", "run this through the planning gates", "get this reviewed before we build it", "turn this idea into governed issues".
allowed_tools:
  - mcp__pfactory__plan_categories
  - mcp__pfactory__plan_ingest
  - mcp__pfactory__plan_process
  - mcp__pfactory__plan_status
  - mcp__pfactory__plan_get
  - mcp__pfactory__plan_list
  - mcp__pfactory__plan_approve
  - Read
  - Bash
---

# /handover-to-pfactory

Hand work to PFactory as a **plan**, not as code. PFactory is the feasibility and
governance gate that sits in *front* of the execution agents: it reviews the plan,
scores it through named gates, requires one human approval, and only then emits
governed GitHub issues plus a signed Task Contract that AIFactory builds and
TFactory verifies.

```
plan_ingest -> plan_process -> plan_status / plan_get -> (human) plan_approve -> emit
```

PFactory does **not** write, run, or test code. If the user wants tests generated
for a finished branch, that is TFactory, not this skill.

## Arguments

- **plan text or path** (required) — inline markdown/Gherkin/EARS, or a path to a
  `.md` / `.pdf` / `.docx`. Exactly one of the two.
- **title** (optional) — defaults to the plan's own heading.
- **category / template** (optional) — the intake choice. Call
  `mcp__pfactory__plan_categories` first to list them; a selected template carries
  rules that are enforced at process time.

## Procedure

### Step 1 — pick a category

Call `mcp__pfactory__plan_categories`. It returns the selectable categories
(product, software, feature, hosting, infrastructure, testing, cicd, ...) and the
templates each one offers. Pick the closest match, or leave both blank.

### Step 2 — ingest

```
mcp__pfactory__plan_ingest(text: "<plan markdown>", category: "infrastructure")
```

or, for a document on disk, `plan_ingest(path: "/abs/path/plan.docx")`. The call
returns a `session_id` and a `board_state`. Handoff from an agent arrives on the
`agent` source channel.

### Step 3 — process

```
mcp__pfactory__plan_process(session_id: "<session_id>")
```

This runs the whole pipeline for the session: detect target kind, enrich with
read-only live context (Kubernetes / OpenShift / AWS / Azure / GCP, Terraform,
Backstage and wikis), code-aware reconnaissance of the target repo, decompose into
an epic plus child issues, assess feasibility (coarse cost, effort rollup, IAM
access simulation, local-cluster build probe), and run the review lenses
(architecture, security, best-practice, feasibility) plus the hard readiness gate.

It is the slow call. Report progress to the user rather than sitting silent.

### Step 4 — read the review back

`mcp__pfactory__plan_get(session_id)` returns the fuller view: the review findings
with **citations**, per-lens scores, the feasibility estimates, and the readiness
report. Summarise for the user:

- `gates_passed` — did the AI review pass?
- Blocking findings by lens, each with its citation. Never restate a finding
  without the citation it came with.
- Cost / effort / access estimates.
- Any hard readiness check that failed. The hard checks are named and inspectable
  (`children-present`, `criteria-present`, `ac-child-coverage`, `deps-sound`,
  `access-granted`, `env-buildable`, `enrichment-integrity`, `language-reconciled`,
  `change-footprint-surfaced`, `constitution-grounded`,
  `deployment-pipeline-present`, `no-blocking-findings`, `decompose-trustworthy`).
  `no-blocking-findings` is never waivable.

If the plan is blocked, the fix is to improve the plan and re-ingest, or to have a
human record a waiver bound to the plan hash (`POST /api/plan/sessions/{id}/waive`
with `check_ids`, `reason`, `waived_by`). Do not offer a waiver as the default
path; it is an auditable human act.

### Step 5 — human approval

Approval is a **human** decision. Present the review, then ask. Only when the user
says yes:

```
mcp__pfactory__plan_approve(session_id: "<id>", approver: "<their name>", feedback: "...")
```

Approval is refused while unwaived hard readiness failures remain
(`plan/review/approval.py`). Never approve on the user's behalf, and never pass a
made-up approver identity.

### Step 6 — emit (HTTP, after approval)

There is no `plan_emit` MCP tool: emission has side-effects, so it lives behind the
HTTP API and is dry-run by default.

```bash
PF=${PFACTORY_URL:-http://localhost:3198}

# governed GitHub epic + child issues
curl -s -X POST $PF/api/plan/sessions/<id>/emit \
  -H 'content-type: application/json' -d '{"repo":"owner/name","dry_run":true}'

# RFC-0002 signed Task Contract v2 -> AIFactory
curl -s -X POST $PF/api/plan/sessions/<id>/emit-contract \
  -H 'content-type: application/json' -d '{"repo":"owner/name","dry_run":true}'
```

Show the user the dry-run output first and get explicit confirmation before
setting `dry_run: false` — a live emit writes issues to their repo and hands the
contract to AIFactory.

## Host without MCP

Every tool is mirrored under `/api/plan/sessions` with identical semantics
(`POST /ingest-text`, `POST /{id}/process`, `GET /{id}`, `POST /{id}/approve`).
See `guides/agent-handoff.md`.

## Notes

- Read-only up to emit. Ingest, process, status and get have no GitHub or
  AIFactory side-effects.
- The plan is the reviewed artifact: waivers and the approval are bound to the
  plan's content hash, and the emitted contract is HMAC signed, so the work that
  ships is provably the work that was approved.
- Pair with `/pfactory-watch` to poll a long-running session through its gates.
