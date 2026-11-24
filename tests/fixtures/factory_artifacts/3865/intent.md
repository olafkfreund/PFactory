---
status: approved
issue: 3865
author: olafkfreund
parent: olafkfreund/Factory#3864 (W1, priority P4)
---

# Intent: one `factory` CLI and MCP front door for Claude, Codex and agy

## Problem

Handing work from a coding agent to the factories takes four clients, four
credentials and several calls:

- PFactory, AIFactory and TFactory each ship a stdio MCP server
  (`*/apps/backend/mcp_server/`), launched from a script inside that
  product's clone (`~/.codex/config.toml:225-263`); CFactory is HTTP MCP
  (`cfactory/mcp.py`). The stdio clients read `$<PORTAL>_MCP_KEY` or
  `~/.<portal>/.mcp-key` (scoped `acw_` keys), falling back to the legacy
  `.token` files (`*/agents/tools_pkg/http_client.py:16-26`). The Codex
  CFactory entry carries its bearer token inline in `http_headers`. agy has no
  factory entries at all.
- Submitting our own approved `intent/`, `spec/` and `plan/` takes four MCP
  calls (`plan_ingest` → `plan_process` → `plan_approve` →
  `plan_emit(dry_run=false)`; `plan_emit` exists since W0, PFactory
  `plan_control.py:282-314`), and `plan_process` re-decomposes the plan
  (`plan/service.py:1471-1474`). The approved steps, the artifact revisions
  and who approved them are lost: the Task Contract has no `source_artifacts`,
  and its `approval` block is the HMAC signing envelope
  (`apis/task-contract.schema.json:33-44`), not a human approval.
- TFactory has no "test this repo/branch/PR" entry: `POST /api/specs/ingest`
  needs a registered `project_id`, a `spec_id` and acceptance-criteria text
  (`routes/specs.py:70-101`), and `create-and-run` does not verify
  (`routes/execution.py:852-875`).
- "Where is my work" means three portals and four project-id schemes
  (AIFactory UUID + `by-slug`, PFactory `<provider>-owner-repo`, TFactory
  path-derived ids, CFactory tenant/repo; RFC-0023). CFactory already joins
  the three by RFC-0001 correlation key (`GET /api/workitems/{key}`,
  `routes_workitems.py:149-168`).

Project-role enforcement (#3857 phase 2, P3) is live in AIFactory 3.21.0,
PFactory 0.9.1 and TFactory 0.14.0. CFactory has only the phase 4 project
view: it scopes by tenant with `read`/`write` API keys (`cfactory/auth.py`)
and has no team/project authorization yet (#3857 plan, CFactory bullet).

## Proposed outcome

Release 1 (this intent's spec and plan), from a clean Claude Code, Codex or
agy session on a host with `factory` installed:

1. `factory submit <slug | plan/<slug>.md>` sends our approved plan through
   PFactory to AIFactory on the trusted-plan fast path in one command,
   keeping the approved steps (no re-decomposition), and returns the
   correlation key. The signed contract carries the source artifacts (path +
   git blob digest + commit) and a human-approval record that the HMAC
   signature covers, separate from the signing-envelope `approval` block.
2. `factory test --repo R --branch B [--pr N] [--criteria FILE | --spec SLUG]`
   schedules a TFactory verification against the cluster and returns its id;
   the acceptance criteria come from the named spec or file, never invented.
3. `factory status <correlation-key | PFactory session | AIFactory task>`
   shows the state across PFactory → AIFactory → TFactory in one answer,
   reusing CFactory's work-item lookup where the caller is allowed to read it,
   and each portal's own status route otherwise.
4. `factory mcp` serves the same verbs as MCP tools.
   `factory mcp-config claude|codex|agy` prints a credential-free entry;
   merging into an agent's config is an explicit flag and keeps existing
   per-portal entries.
5. Credentials in release 1: each portal's existing scoped key, resolved in
   the stdio clients' order (`*_MCP_KEY`, `.mcp-key`, then legacy token
   file), behind one client.

Release 2 (fleet login, its own intent/spec/plan under #3864, gated): one
login yielding short-lived per-portal-audience tokens with revocation, checked
as scope ∩ project grant at every portal, `factory login|logout`. Gate: route
level evidence of project enforcement on all four portals, CFactory included.

## Affected users and systems

- Owner and agents (Claude Code, Codex, agy) on p620 and other NixOS dev hosts.
- Factory: new package under `shared/` (CLI + MCP); `apis/task-contract.schema.json`.
- PFactory: an additive route (or flag on ingest) that accepts pre-approved
  steps plus artifact provenance and skips decomposition; same approval and
  emit checks as `plan_emit`.
- AIFactory: accept and keep the new contract fields on `/api/tasks/from-plan`.
- TFactory: an additive repo/branch/PR verify entry built on specs ingest.
- CFactory: read-only reuse of `/api/workitems/{key}`.
- Existing per-portal MCP servers and the `parr-run` skill keep working.

## Constraints

- No lockout: the legacy shared token, scoped `acw_` keys, local mode and
  existing MCP entries keep working (P3 no-lockout rule).
- No new orchestrator. The CLI is a client; the PFactory and TFactory entries
  above are small additive routes, not a pipeline of their own.
- Approval evidence must be verifiable, not an editable frontmatter line:
  release 1 binds the approval to the approving commit on the task branch
  (`docs(plan): approve …`, its author and SHA) and to the authenticated
  submitting key, and PFactory's `require_user_approval` check still applies.
  The human-approval record and artifact digests sit inside the signed
  contract body so they cannot change after signing.
- Same answers as the portals: unknown and invisible projects return the
  identical 404 (D6) through the CLI and MCP; cross-portal id resolution must
  not reveal a project in another org. CFactory status is used only where the
  caller's key may read it, until CFactory has project authorization.
- A rejected submit fails with the portal's safe reason; a timeout reports
  "outcome unknown" with the correlation key, never "failed"; nothing falls
  back to a re-planned build, whatever the legacy fallback setting.
- Tokens are never printed, logged or written into generated configs; configs
  reference key files or env vars.

## Open questions

1. Language and packaging (one decision): Python package under
   `shared/factory-cli`, shipped as a Nix flake output; non-NixOS hosts use
   `uv tool install` from the repo. Default: yes.
2. Fleet login as its own intent under #3864 after release 1. Default: yes.
3. Approval evidence for `factory submit`: approving commit on the task
   branch + authenticated submitting key (above). Alternative: require a
   merged PR. Default: approving commit; a merged-PR rule can be added per
   repo by `.factory.yml` (W3).
4. `factory status` ids: correlation key, PFactory session id and AIFactory
   task id. Default: all three, resolved to the correlation key.
5. Should CFactory team/project authorization (#3857) be pulled forward as a
   separate issue now, since release 2 depends on it? Default: open the issue
   now, schedule it with release 2.
