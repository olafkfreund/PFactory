# Design: Plan → Documentation emit (TechDocs / Backstage / Confluence) + cross-factory memory

> Status: **Draft for review** · Created: 2026-06-10 · Owner: olafkfreund
> Scope: a new `emit`-stage capability that turns an approved PFactory plan into
> durable documentation across the repo (Backstage TechDocs), the Backstage
> catalog (annotations), and Confluence — with a machine-readable index that the
> other factories use as shared memory / a dependency + docs lookup.

## 1. Summary

When a plan is approved/emitted, PFactory already emits **GitHub epics** (for
execution) and a **Task Contract** (for AIFactory). This adds a third emission:
**documentation**. One deterministic render of the plan fans out to:

1. **Repo / TechDocs** (always): `techdocs/plans/<plan-id>.md` + a `Plans` nav
   section + a `techdocs/plans/index.md` registry page. Because the Backstage
   instance builds TechDocs **inline from the repo** (`builder: 'local'`), this
   *is* publishing to Backstage — no S3/CI.
2. **Backstage catalog** (when reachable): the plan pages are attached under the
   existing `Component:pfactory` (TechDocs), and a machine index
   (`techdocs/plans/registry.json`) records `correlation_key → {plan-id, deps,
   doc-url}`. (Per-plan catalog *entities* are explicitly out of scope — see §6.)
3. **Confluence** (when configured): a page per plan, upsert-by-title, labelled.

The registry + TechDocs become **shared memory**: AIFactory / TFactory / CFactory
look up "the plan, requirements, and dependencies behind epic #N" by
`correlation_key`, via the existing runtime MCP (`pfactory_get_*`) **and** this
durable, browsable doc surface.

**Everything is optional with a safe default.** If nothing is configured, the
emit writes **only the repo/GitHub docs** (always available). Backstage and
Confluence are opt-in — enabled per deployment/user via **Settings** (§6e) and
selectable **per-plan** at emit time (§6d). Backstage/Confluence are never
required; their absence degrades silently to repo-only.

**TFactory reuses this.** The render-agnostic core (target protocol + the three
targets + the registry) is generic; once shipped here, TFactory emits its
**test results** through the same machinery (§10.5).

## 2. Why (the key insight)

The Backstage instance (`p510`) runs `builder/generator/publisher: 'local'`: it
runs `mkdocs build` from the repo checkout on first Docs-tab view (cached
`ttlMinutes: 1440`). Therefore **repo Markdown + an imported catalog entity =
live Backstage docs.** "Emit to repo" and "emit to Backstage" are the *same*
write at the doc layer; Backstage and Confluence are projections of the repo
substrate. (Source: project memory `pfactory-backstage`.)

Second: PFactory already **reads** Backstage/Confluence as enrichment
(`plan/enrich/knowledge/{backstage,confluence}.py`, read-only). Adding the write
side makes PFactory a **bidirectional knowledge fabric** reusing the same HTTP
clients/auth — but introduces a **feedback-loop risk** (a plan emits a doc that a
later plan's enrichment then ingests). Mitigated in §6 with a `generated`
provenance marker.

## 3. Goals / non-goals

**Goals**
- Deterministic, idempotent render of the full plan model into Markdown.
- **Optional targets with a safe default:** repo/GitHub always; Backstage &
  Confluence opt-in, selectable per-plan and configured in **Settings**
  (encrypted, user/org-scoped) — never required.
- Repo/TechDocs target (always available) → Backstage rendering for free.
- Backstage catalog wiring (annotations on the existing Component) + a
  machine-readable registry of plans for cross-factory lookup.
- Confluence target (REST upsert) behind config.
- Cross-factory read path documented + (optionally) wired into `BackstageConnector`.
- Honor the "no automatic pushes" policy: dry-run-first git writes.

**Non-goals (this iteration)**
- Per-plan Backstage **catalog entities** / real `dependsOn` *edges* (§6).
- Editing/round-tripping docs back into the plan model.
- Bidirectional Confluence sync (we only push).
- Auth/SSO changes to Backstage or Confluence.

## 4. Architecture

New package `apps/backend/plan/emit/docs/`, a sibling of `github_emitter`:

```
plan/emit/docs/
  render.py            # plan model -> DocBundle (pure, deterministic, testable)
  bundle.py            # DocBundle dataclass: plan_id, title, markdown, registry_entry, meta
  targets/base.py      # DocsTarget protocol: name, available() -> bool, publish(bundle) -> TargetResult
  targets/repo.py      # RepoDocsTarget       (always available)
  targets/backstage.py # BackstageTarget      (catalog refresh + techdocs sync + registry)
  targets/confluence.py# ConfluenceTarget     (REST page upsert)
  emit_docs.py         # orchestrator: render once -> publish to each available target
```

Integration point: `PlanService.emit()` (`plan/service.py:530`), after
`emit_to_github(...)` succeeds and before/after `notify_completion(session)`.
Gated and dry-run-first (see §9). Returns a `docs_result` stored on the session
(`session.docs_result`) and surfaced in the API/portal.

`DocsTarget` is a `Protocol` (mockable in tests, matching the codebase seam
style). The renderer is pure (no network) so it unit-tests without any target.

## 5. The render (plan model → docs)

`render.py` maps the existing model to a single Markdown page per plan:

| Section | Source |
|---|---|
| Front-matter / Overview | `NormalizedPlan.title`, `description`, `target_kind`, `plan_type` |
| Provenance | `correlation_key`, `emitted_issue_number`, `plan_id`, `content_hash`, source (`github_issue #N`) |
| Acceptance Criteria | `NormalizedPlan.criteria[]` |
| Decomposition | `EpicPlan.epic_title/epic_body` + `children[]` (story points, links to emitted GitHub issues) |
| Feasibility | `EpicPlan.cost_estimate.lines[]` (table), `effort_estimate`, `access_requirements` |
| Governance | `PlanReview` gates passed/failed, findings, approval, waivers (audit trail) |
| Dependencies & context | `enrichment.infra[]` + `enrichment.knowledge[]` (links to grounding docs → the dependency list) |

The same render produces the **registry entry** (machine view):

```json
{
  "correlation_key": "34",
  "plan_id": "006-bench-fastapi-...",
  "title": "...",
  "epic": 34,
  "target_kind": "software",
  "plan_type": "feature",
  "doc_path": "techdocs/plans/006-....md",
  "doc_url": "<backstage techdocs url>",
  "dependencies": ["api:foo", "service:bar", "<knowledge refs>"],
  "content_hash": "…",
  "generated_by": "pfactory",
  "updated_at": "…"
}
```

## 6. Targets

### 6a. RepoDocsTarget (always available — the substrate)
- Writes `techdocs/plans/<plan-id>.md`.
- Maintains `techdocs/plans/index.md` (human "Plans" index, grouped by
  board_state/plan_type) and adds a `Plans` entry to `mkdocs.yml` `nav` (once).
- Writes/updates `techdocs/plans/registry.json` (the machine index, §5).
- **Dry-run-first**: by default renders to the workspace and reports a diff; with
  `PFACTORY_DOCS_GIT_WRITE=1` commits to a branch (never force-pushes; no
  automatic push to `main`) — mirrors `PFACTORY_TRIAGER_GIT_WRITE`.
- This target alone yields Backstage-rendered docs (builder:'local').

### 6b. BackstageTarget (catalog annotations + memory index)
Decision: **annotations on the existing `Component:pfactory`, not per-plan
entities.** Consequences and the chosen design:
- Plan pages render under `Component:pfactory`'s TechDocs (they're in `techdocs/`).
- The plans list + dependencies live in `index.md` + `registry.json` (the
  machine graph), **not** as catalog `dependsOn` edges. Real per-plan edges would
  require per-plan entities — deferred (future option, gated on whether the
  instance rules allow `kind: Resource`; `Domain` is rejected wholesale).
- After a repo write, trigger discovery: `POST /api/catalog/refresh` then
  `GET /api/techdocs/sync/default/component/pfactory` (provider scan lag is real;
  see memory). Honors instance rules: flat `Component`, `owner: olafkfreund`,
  `domain: public`, **never `kind: Domain`**.
- Optionally add a single catalog annotation `pfactory.io/plans-registry:
  <raw url to registry.json>` on `Component:pfactory` so consumers discover the
  index from the catalog.

### 6c. ConfluenceTarget (org wiki)
- Reuse `plan/enrich/knowledge/confluence.py`'s client/auth pattern (lift the
  HTTP client to a shared `confluence_client.py`).
- Upsert a page per plan **by title** under a configured space; set labels
  (`pfactory`, `plan`, `plan-type:<…>`, `correlation:<key>`); body = the rendered
  Markdown → Confluence storage format (or `wiki` macro).
- Idempotent: look up by title/label; update if exists, else create.

### 6d. Target selection (optional, default = GitHub/repo)
The emit resolves an effective target set, in precedence order:

1. **Per-plan request** — the portal "Emit" action (and the `POST …/emit` body /
   the MCP `plan_emit` tool) accepts an optional `doc_targets: ["repo",
   "backstage", "confluence"]`. When present, it wins.
2. **Settings default** — each configured connection (§6e) has an `enabled by
   default` toggle. Enabled, available connections are added.
3. **Fallback** — `repo` is **always** included and is the sole target when
   nothing else is selected/available.

A target is only ever run if it is BOTH selected AND `available()` (has a valid
connection). So "use GitHub if nothing else is specified; choose Backstage or
Confluence when needed" is the literal behaviour: repo by default, the others
on explicit opt-in.

### 6e. Settings — Backstage & Confluence connections
Connections are first-class, stored like the existing ones (`LLMEndpoint`,
`TestTargetCredential`, `GitCredential`): **user/org-scoped, secret encrypted at
rest** via `crypto.encrypted_string.EncryptedString`.

- **DB model** `DocsTargetConnection` (`apps/web-server/server/database/models.py`):
  `id, user_id, org_id, kind ('backstage'|'confluence'), label, base_url,
  api_token (EncryptedString), space (Confluence), enabled_by_default (bool),
  last_used_at, created_at`.
- **Routes** `routes/docs_targets.py` — `GET/POST/DELETE /api/docs-targets`
  (+ `POST /api/docs-targets/{id}/test` connectivity probe), mirroring
  `routes/llm_endpoints.py`.
- **Frontend** `components/settings/sections/DocsTargetsSettings.tsx` — a new
  Settings section ("Documentation Targets") with two sub-forms:
  - **Backstage:** base URL (e.g. `https://p510…/backstage`), API token, a
    **Test** button (`GET {base}/api/catalog/entities?limit=1`), enable-by-default.
  - **Confluence:** base URL, API token, space key, **Test** button
    (`GET {base}/wiki/rest/api/space/{space}`), enable-by-default.
  Wired into `AppSettings.tsx` nav (a `docsTargets` tab), reusing the
  `GitCredentialsSettings`/`LLMAccountsSettings` UX (list · add · test · remove,
  secret shown masked).
- **Cluster default (no UI needed):** env (`BACKSTAGE_BASE_URL`,
  `CONFLUENCE_*`) seeds a deployment-wide connection so the in-cluster service
  works without a per-user setup — same precedence (Settings overrides env).
- **Secret wiring:** Confluence/Backstage tokens added to `factory-secrets`
  (gitops), same pattern as `OLLAMA_API_KEY`.

## 7. Cross-factory consumption (how it's used as memory)

Two surfaces, one key (`correlation_key`):
- **Structured / live:** the MCP planning-context server (`pfactory_get_epic/
  _requirements/_decomposition/_review_status`) — already shipped.
- **Durable / browsable + graph:** `techdocs/plans/registry.json` (machine) +
  TechDocs pages (human). Other factories already have `BackstageConnector`
  (read-only catalog) — extend it (P4) with `resolve_plan(correlation_key)` that
  reads `registry.json` (via the catalog annotation or raw GitHub) and returns
  `{doc_url, dependencies, epic}`.
- **Feedback-loop guard:** every emission is marked `generated_by: pfactory` /
  `pfactory.io/generated: true`; the enrichment `BackstageConnector`/
  `ConfluenceConnector` **skip PFactory-generated docs** so a plan never ingests
  its own emissions.
- Optional: also write a Graphiti node keyed by `correlation_key` for semantic
  recall (the existing memory system).

## 8. Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PFACTORY_DOCS_EMIT` | off | master switch for the docs emit stage |
| `PFACTORY_DOCS_GIT_WRITE` | off | actually commit the repo docs (else dry-run diff) |
| `PFACTORY_DOCS_BACKSTAGE` | off | run the Backstage refresh/sync target |
| `BACKSTAGE_BASE_URL` | — | e.g. `https://p510.tail833f7.ts.net/backstage` |
| `PFACTORY_DOCS_CONFLUENCE` | off | run the Confluence target |
| `CONFLUENCE_BASE_URL` / `CONFLUENCE_API_TOKEN` / `CONFLUENCE_SPACE` | — | Confluence target |
| `PFACTORY_DOCS_DIR` | `techdocs/plans` | doc output dir (override for tests) |

All default **off**; each target's `available()` degrades gracefully to repo-only.
**Connections live in Settings (§6e), encrypted + user/org-scoped; the env vars
above are only the cluster-wide default/fallback.** Settings overrides env.

## 9. Safety / correctness

- **Idempotent** via `content_hash`: unchanged plan ⇒ no re-commit, no Confluence
  re-push, no churn.
- **No automatic pushes** (project policy): repo writes are dry-run-first; commits
  go to a branch only on opt-in; pushing to `main` is never automatic.
- **Best-effort, never fatal:** a failing target (Backstage unreachable,
  Confluence 401) logs + records a per-target result; it never breaks
  `emit`/`notify_completion`.
- Pure renderer + protocol targets ⇒ fully unit-testable without network.

## 10. Phased delivery + acceptance tests

- **P1 — Repo/TechDocs.** `render.py` + `RepoDocsTarget` + mkdocs nav + index +
  registry.json; wired into `emit` (dry-run). *Tests:* render is deterministic
  for a fixed plan; idempotent on re-render; registry.json round-trips; a
  Backstage `mkdocs build --strict` over `techdocs/` still passes with the new
  `Plans` nav.
- **P2 — Backstage catalog.** refresh + techdocs-sync + the `plans-registry`
  annotation + `generated` provenance. *Tests:* sync helper hits the right
  endpoints (mocked); enrichment connectors skip `generated` docs.
- **P3 — Confluence.** REST upsert (reuse client). *Tests:* create-vs-update by
  title (mocked client); labels applied; idempotent on unchanged hash.
- **P4 — Settings connections + cross-factory read.** `DocsTargetConnection`
  model + `routes/docs_targets.py` + `DocsTargetsSettings.tsx` (Backstage +
  Confluence, with Test buttons, enable-by-default) wired into the precedence in
  §6d; `BackstageConnector.resolve_plan(correlation_key)`; doc for
  AIFactory/TFactory/CFactory; optional Graphiti node. *Tests:* a configured
  connection is used and overrides env; `resolve_plan` returns doc_url + deps.

### 10.5 TFactory adoption (test results → docs)
Once this ships in PFactory, factor the generic core — the `DocsTarget`
protocol, the three targets (`repo`/`backstage`/`confluence`), the
`registry.json` index, and the selection/Settings machinery — so it is **not**
plan-specific. TFactory then provides only its own `render_test_results(...)`
(triage report → Markdown: lanes, verdicts, coverage delta, mutation/stability,
flaky history) and reuses the same targets, the same `DocsTargetsSettings`, and
the same `correlation_key` so a test-result doc sits next to the plan + epic it
verifies (closing the PARR doc trail: plan → code → **verify**). Tracked by a
TFactory issue filed at delivery (mirrors `TFactory#326`).
- *Shared-core option:* lift `plan/emit/docs/` into a small reusable module the
  factories vendor, or duplicate-then-converge (TFactory is a fork, so a copy is
  low-friction first). Decide at P4.

## 11. Risks / open questions

- **Catalog graph fidelity.** "Annotations on Component" trades real per-plan
  `dependsOn` edges for a `registry.json` index. If/when machine graph traversal
  is needed, revisit per-plan `kind: Resource` entities (verify instance rules).
- **mkdocs nav growth.** Many plans → a long `Plans` nav. Mitigate with a single
  `index.md` (auto-listing) + flat `plans/` dir rather than per-plan nav entries.
- **Confluence storage format.** Markdown→storage fidelity (tables, code) needs a
  converter; start with a fenced-`wiki`/`markdown` macro and refine.
- **Provider lag.** New Backstage docs appear a few minutes after the catalog
  scan; the sync trigger helps but isn't instant — set expectations in the UI.
- **Secrets.** Confluence creds must be added to `factory-secrets` (gitops),
  same pattern as `OLLAMA_API_KEY`.
