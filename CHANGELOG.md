# Changelog

## 0.6.8 — bash sandbox toggle for k3d (2026-06-10)

- **Agent bash no longer breaks under the OS sandbox on k3d (AIFactory #363).** Mirrors AIFactory v3.6.9: bwrap can't mount `/proc` on k3d (the node is a container), so the SDK's bash sandbox broke every agent command. Gated `sandbox.enabled` behind `AIFACTORY_BASH_SANDBOX` (default on); set `false` on the cluster — bash works, isolation via the K8s pod boundary + command allowlist until gVisor lands. Also includes the agent-sandbox apt deps (#98) + GHCR_PAT push fix shipped earlier on main.
## 0.6.7 — Restore the animated Planning rail (2026-06-09)

> Bring back the animated ring-medallion pipeline rail on the Planning Board —
> this time correctly driven by plan sessions.

- **Animated PipelineRail restored.** v0.6.5 swapped the task `KanbanBoard`
  (which rendered the rail) for the static `PlanBoard` and lost it. `PlanBoard`
  now renders `PipelineRail` at the top, fed by the same plan sessions the
  columns show (grouped by `board_state`) — ring medallions, count badges,
  animated arcs/halos, and marching-ant connectors are back.

## 0.6.6 — Plans survive restarts (disk persistence) (2026-06-09)

> The PlanService store was in-memory only, so every pod restart/redeploy wiped
> all plans. Add opt-in disk persistence so they survive.

- **Persistent plan sessions.** With `PFACTORY_PLAN_PERSIST=1`, every plan
  mutation is mirrored to `<store_dir>/<id>.json` (atomic temp+rename) and the
  set is reloaded on startup. Store dir defaults under `~/.pfactory` (the
  deployment's PVC), override via `PFACTORY_PLAN_STORE_DIR`. Off by default so
  unit tests stay hermetic; load/save are best-effort (missing dir → empty,
  corrupt file skipped, disk hiccup never breaks a request).

## 0.6.5 — Unified Planning board + trimmed nav (2026-06-09)

> Planning portal fixes: one data source behind List and Board, and a leaner
> left nav.

- **Planning List & Board show the same data.** Board mode now renders the
  plan-session kanban (`PlanBoard`, bucketed by `board_state`) over the same
  global plan-store sessions the List view uses — previously Board showed the
  project-scoped AIFactory *task* board, which sat empty when sessions existed
  but no project/tasks were selected. Selecting a board card opens its detail in
  the List pane.
- **Trimmed left nav.** Removed the **Worktrees** and **Changelog** items from
  the sidebar.

## 0.6.4 — Per-user API tokens + Ollama Cloud provider (2026-06-09)

> Ship the merged #93/#94 work in a deployable image: personal API tokens for
> programmatic access, and Ollama Cloud as a first-class LLM provider.

- **Per-user API tokens on `/api/*` (#93).** `TokenAuthMiddleware` now accepts a
  personal `acw_` token as a `Bearer` on the REST API, gated on a new `api`
  scope so MCP-only keys stay scope-isolated. Expired/revoked keys are rejected
  and `last_used_at` is recorded. The mint UI gains the `api` scope (section
  relabelled "API Tokens"). Lets MCP / `/handover` clients authenticate with an
  attributable, revocable per-user token instead of the shared `APP_API_TOKEN`.
  See `guides/api-tokens.md`.
- **Ollama Cloud provider (#94).** Hosted OpenAI-compatible inference at
  `https://ollama.com`, authed by `OLLAMA_API_KEY`, mirroring the GitHub Models
  wiring — routes through the openai-compatible backend. Models route to it via
  a `:cloud`/`-cloud` suffix (e.g. `glm-5:cloud`) or an explicit `ollama-cloud:`
  prefix (e.g. `ollama-cloud:gpt-oss:120b`). Config via `OLLAMA_CLOUD_BASE_URL`
  (default `https://ollama.com`) + `OLLAMA_API_KEY`. See `guides/ollama-cloud.md`.

## 0.6.0 — Backstage onboarding + observability instrumentation (2026-06-05)

> Make PFactory a first-class citizen of the platform: register it in the
> Backstage software catalog with TechDocs, and start emitting the token
> usage CFactory needs to attribute cost across the Factory family.

- **Completion-event token usage (#60).** PFactory's completion event to
  CFactory `/api/events` now carries the additive RFC-0001 v1.1 `usage` block
  (`input_tokens` · `output_tokens` · `total_tokens` · `cost_usd` · `model`),
  so the cockpit **Tokens & cost** page can attribute spend to PFactory instead
  of showing it as *"not instrumented yet."* A new zeros-safe `PlanUsage`
  accumulator (`plan/usage.py`) sums the Plan run's LLM seams; the block is
  honestly zero on the deterministic path and reports real tokens/cost the
  moment an LLM seam runs. Additive and optional — no schema break.
- **Backstage catalog + TechDocs (#55–#59).** PFactory is onboarded to the
  Backstage software catalog with enriched annotations, a `pfactory-portal`
  component (`subcomponentOf pfactory`), TechDocs (MkDocs) wired with a
  strict-build, CI catalog validation, and the Backstage AI skills installed.
- **Port move (#54).** The portal moves to **3114** (backend) / **3115**
  (frontend) to avoid local collisions across the Factory suite.

## 0.5.0 — Bidirectional AIFactory ↔ PFactory integration (2026-06-03)

> Close the loop with AIFactory: when PFactory's tests find problems, hand a
> **correction** back to AIFactory for a fix, then re-test — bounded so it can't
> run away. The reverse of `/handover-to-pfactory`. Epic
> [#182](https://github.com/olafkfreund/PFactory/issues/182).

- **Hand-back pipeline (#182).** New `agents/handback/` packages a finished
  run's failures into a `QA_FIX_REQUEST.md`-shaped correction and (opt-in) sends
  it to AIFactory's QA Fixer:
  - **Traceability (#183).** The snapshotter records the AIFactory hand-back
    target (`aifactory{project_id,spec_id,api_url,task_id}` + `correction_cycle`)
    in `context/source.json`; `api_url` defaults to AIFactory's web-server
    (`http://localhost:3101`, override `PFACTORY_AIFACTORY_API_URL`).
  - **Builder + renderer (#184).** `request.py` + `render.py` — pure-compute
    selection of failing tests → a deterministic fix-request payload (reuses the
    triage report + the visual correction plan).
  - **Sender + Triager hook (#185).** `send.py` writes
    `findings/handback_request.{md,json}` always and POSTs only on
    `dry_run=False AND confirm`; the Triager's terminal-status hook *prepares*
    (default ON) and *sends* on opt-in (`PFACTORY_HANDBACK_SEND=1`), mirroring
    `PFACTORY_TRIAGER_GIT_WRITE`.
  - **Operator skill + CLI (#186).** `/handback-to-aifactory` (+ AIFactory
    companion) previews then sends via the AIFactory MCP tool
    `task_apply_correction`, or `python -m agents.handback <spec_dir> --send`.
  - **Bounded closed loop (#187).** `loop.py` + `/pfactory-fixloop` drive a
    test→fix→re-test cycle that stops at **passed**, or **stuck** (cap
    `PFACTORY_HANDBACK_MAX_CYCLES` default 2, or no progress).
  - **AIFactory receiver** (sister repo, `AIFactory#317`): `POST
    /api/tasks/{task_id}/apply-correction` + MCP `task_apply_correction` write
    `QA_FIX_REQUEST.md` onto the original spec and run the existing QA Fixer.
  - Dry-run-first + opt-in throughout (no automatic pushes). See
    `guides/aifactory-handback.md` and
    `docs/plans/2026-06-03-aifactory-pfactory-handback-design.md`.

## 0.4.0 — Visual Inspection Run + SaaS connector targets (2026-06-03)

> A new **Visual Inspection Run** feature (all no-tenant phases shipped) plus the
> first-class **SaaS connector** target. Epic
> [#170](https://github.com/olafkfreund/PFactory/issues/170).

- **Visual Inspection Run (#170).** Record a generated Playwright **browser** run
  (trace + video + step-labeled verification *and* error screenshots) and package
  it into `automated-test/<YYYY-MM-DD-HHMMSS>/` with a deterministic human report,
  an LLM correction plan (injectable seam + deterministic fallback), a GitHub
  issue export (dry-run), and `meta.json`. New `agents/visual_inspection/`
  (`packager` · `report` · `correction_plan` · `issues` · `store`); a
  `write_paths_to_branch` git helper to commit the folder to the SUT repo
  (dry-run default); a portal **Visual Reports** page + `/api/visual-inspections`
  routes; and a `/handover-to-pfactory` opt-in (`visual_inspection {enabled,
  target, flow}` threaded through `task_create_and_run`). Phases P1/P2/P4/P5
  shipped; **P3** (ServiceNow browser/SSO) remains, needing a live tenant.
  See `docs/plans/2026-06-03-visual-inspection-run-design.md`.
- **SaaS connector target (#111).** A first-class `type: connector` target
  (ServiceNow / Salesforce / SAP / MuleSoft) reusing the http + credential-vault
  auth, plus a platform registry mapping each platform → API style · `library/`
  check template · guidance. See `guides/saas-connectors.md`.
- **storageState login-once scaffolding (#107).** Gen-Functional scaffolds
  `auth.setup.ts` + a `requires_auth` Playwright config from a ref-auth target's
  selectors, so a browser test logs in once and reuses the session.

## 0.3.0 — Cloud testing (AWS/GCP/Azure), platform foundations + portal redesign (2026-06-03)

> The cloud epic ships end-to-end across three providers, four backlog
> capabilities get their foundations wired + tested, and the portal gets a
> flagship-grade design pass. Headlines below; the credential-broker fast-follow
> work is folded in from the prior Unreleased section.

- **Cloud infrastructure testing — AWS · GCP · Azure (epic #133, complete).**
  A read-only assessment flow: access gate → discovery (host `aws`/`gcloud`/`az`)
  → Mermaid topology → Prowler/CIS scan (OCSF) in `pfactory-runner-cloud` →
  accept/flag/reject verdict → downloadable remediation plan. GCP uses ADC,
  Azure uses `--az-cli-auth` (the image bundles `azure-cli`); all three
  live-verified read-only against real accounts. `frameworks/cloud-discover` +
  `frameworks/cloud-prowler` descriptors + a high-signal check library (#138).
  Launch from the portal — **+Task → Cloud Infrastructure** runs the discovery
  gate first, then backgrounds the assessment; reports land in **Cloud Reports**.
  See `guides/cloud-testing.md`.
- **Test-target login credentials wired into the lanes (#107).** A
  `auth: {type: ref}` target's credential is resolved and injected
  (`TEST_USERNAME`/`TEST_PASSWORD`) into the api/browser run, egress-gated and
  wiped after — so a generated test can log in to a SUT, then test it.
- **Kubernetes port-forward dispatch wired into the Evaluator (#108).** A
  `type: kubernetes`, `port_forward: true` target is `kubectl port-forward`-ed
  for the run lifetime (auth via the read-only kubeconfig), its
  `http://localhost:<port>` injected as `PFACTORY_TARGET_URL`, torn down on
  success and failure.
- **Visual-regression baselines surfaced in the portal (#109).** A portal API
  over `agents.evidence.visual_baseline` — list / serve / accept (traversal-
  guarded) — backing the Playwright `toHaveScreenshot` template + the
  threshold/anti-flake guidance already shipped.
- **Portal redesign.** Flagship-grade pass: home Tests list + Cloud Reports as
  card layouts with humanised time and verdict-driven status colour; a branded,
  atmospheric login; a grouped top-bar status cluster; the new Gruvbox flask
  favicon; and a fix for the New Task dialog's leftover Ocean-theme colours.

- **Fix: Evaluator no longer fails on a verdicts.json with trailing data.** The
  LLM sometimes wraps the JSON in a ```` ```json ```` fence or appends a
  sentence after it, which made strict `json.loads` raise *"Extra data"* and
  the task land in `evaluator_failed` / `evaluator_invalid_verdicts`. The
  validator now parses leniently (fence-strip + `raw_decode` of the first JSON
  value) and rewrites the salvaged object so the Triager reads clean JSON.

- **Workload-identity federation (#74).** Mint short-lived scoped credentials
  from an OIDC token via a `wif` block in `~/.pfactory/credentials.json`. AWS
  STS `AssumeRoleWithWebIdentity` is implemented (`pfactory_secrets/wif.py`);
  `resolve_cloud("aws")` returns short-lived keys that the broker caches with
  their TTL and re-mints near expiry. GCP WIF / Azure federated tokens routed
  (fast-follow). Completes the Credential Broker epic (#62).
- **Sandbox credential injection (#73).** Network-enabled lanes (api /
  integration) can authenticate inside the Docker sandbox — broker-resolved
  cloud tokens as env vars plus a kubeconfig bind-mounted read-only — gated by
  `network != none` **and** egress opt-in. The unit lane (`--network=none`)
  gets nothing; mounted secret files are wiped after the run. Seam:
  `tools/runners/sandbox_credentials.py` + `DockerRunner.run(secret_files=…)`.
- **Operator credential config (#71).** Formalised the
  `~/.pfactory/credentials.json` (0600) schema/loader
  (`pfactory_secrets/operator_config.py`): a `cloud` block (provider → backend
  ref) plus a `credentials` block of named sets, the host-wide analogue of the
  per-project `.pfactory.yml` `credentials:` (project wins on collision).
- **Triager completion callback (#85).** When a task reaches a terminal
  status, the Triager can notify a watcher so `/pfactory-watch` needs no
  polling. Two opt-in, best-effort channels (both OFF by default; a failing
  target never breaks the pipeline): `PFACTORY_COMPLETION_WEBHOOK=<url>`
  (POSTs `{task_id, project_id, status, phase, updated_at}`) and
  `PFACTORY_COMPLETION_SENTINEL=1` (writes `findings/COMPLETED.json`).

## Unreleased — Credential Broker (epic #62)

> Agents can now authenticate to cloud environments using vault-backed or
> locally-encrypted credentials, gated by an honest egress posture. See
> `guides/credentials.md` and `docs/plans/2026-05-30-credential-broker-design.md`.
>
> **Pluggable secrets backends** (`apps/backend/pfactory_secrets/`). A
> `SecretsBackend` abstraction + factory (mirrors `providers/factory.py`) +
> ref routing (`env:` · `sops:`/`age:`/`agenix:` · `vault:` · `azurekv://` ·
> `aws-sm://` · `gcp-sm://`). Cloud SDKs import lazily — an absent package makes
> only that backend unavailable. (#63 #64 #66 #67 #68 #69)
>
> **CredentialBroker** (#65) extends the existing `core/mcp_credentials.py`
> chain with a vault-fetch head, materialises file creds (kubeconfig, GCP ADC)
> at **0600** in a per-task scratch dir, **wipes** them on task end, and wires
> resolved env into the agent via `core/client.py` (off unless egress is on).
>
> **Honest egress** (#8): `.pfactory.yml` `egress.enabled` gate (default OFF) +
> `credentials:` block; a secret-free egress **manifest** + badge; log redaction;
> `python -m pfactory_secrets.cli audit|doctor|resolve`.
>
> Fast-follows: sandbox-test injection (#73), OIDC/workload-identity
> federation (#74). ~99 new tests across the secrets suite.

## 0.2.2 — Multi-provider hardening: Codex auth + GitHub Copilot CLI (2026-05-30)

> Makes the non-Claude provider lanes reliable for demos and BYO-LLM.
>
> **Codex now works regardless of your global `codex login`.** A bare
> ChatGPT-account login rejects every model ("model is not supported when
> using Codex with a ChatGPT account"), which silently broke PFactory's Codex
> lane. `codex_agentic.py` now provisions a PFactory-owned `CODEX_HOME`
> (`~/.pfactory/codex-home/`) with an api-key `auth.json` built from
> `OPENAI_API_KEY` and points the `codex mcp-server` subprocess at it — your
> global login is left untouched. It also surfaces MCP run errors instead of
> swallowing them as "(no output from Codex MCP)", resolves the `codex` shell
> alias to `codex-cli`, and defaults to `gpt-5.3-codex`. Verified end-to-end:
> Planner emits a valid plan even with the broken ChatGPT login still active.
>
> **New provider: GitHub Copilot CLI** (`copilot:<model>`). A first-class
> agentic provider (`copilot_agentic.py`) that runs `copilot -p "<prompt>"
> --allow-all-tools --model <model>` headlessly — Copilot runs its own tool
> loop in one shot. Models: `claude-sonnet-4.5` (default), `claude-sonnet-4`,
> `gpt-5`, billed to your Copilot subscription. Routed via the factory
> (`copilot:gpt-5` correctly resolves to Copilot, not Codex). Verified
> end-to-end: Planner produced a 7-subtask plan on `copilot:claude-sonnet-4.5`.
>
> **Ollama / local models can now write the workspace.** Ollama is the only
> provider whose file ops run through PFactory's own `ToolExecutor`, which was
> sandboxed to a single root (the SUT project dir). The Planner/Gen-Functional/
> Evaluator write into the spec/workspace dir — *outside* that root — so every
> `Write` was denied and the model burned all its turns retrying (looked like a
> convergence failure; it wasn't). `ToolExecutor` now takes `extra_roots`, and
> the spec dir is threaded in for the Ollama provider. Verified:
> `qwen2.5-coder:14b` Planner went from 25-turn timeout to **PASS (3 subtasks)
> in ~36s**.

## 0.2.1 — Version honesty + docs reconciliation (2026-05-30)

> Housekeeping release. No runtime behaviour change.
>
> **Version stamp corrected.** `package.json`, `apps/frontend-web/package.json`,
> and `apps/backend/__init__.py` were still on the inherited AIFactory `3.0.2`
> despite the product being on the v0.x line. Now `0.2.1` — the honest next
> patch after the v0.2.0 release. (Heading uses the bare `## 0.2.1` form that
> `release.yml` validates, unlike the older `## vX.Y.Z` entries below.)
>
> **Docs reconciled to the v0.2 lane spine.** README + CLAUDE.md no longer
> describe the retired v0.1 `Functional / SAST / DAST / Fuzz` lanes; security
> scanning is stated as out of scope (delegated to dedicated pipelines). See
> issues #34 / #35 under epic #33 (Product Hardening & Market Fit).

## v0.2.0 — Enterprise Test Framework Spine (2026-05-29)

> All 16 v0.2 tasks shipped + the deferred Task 16 follow-up (Triager
> evidence-links). Tagged + released:
> <https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0>.
>
> **Backend tests: 1177 passing** (was 531 at v0.1.0-mvp — **+646**).
> **Frontend tests: 49 (LaneStatusGrid) + 187 (PFactoryTaskDetail with
> evidence tab) + the wider suite.**
>
> **Task 16 follow-up (deferred commit 4) landed:** Triager now surfaces
> portal evidence links per accepted/flagged candidate in
> `triage_report.md`, so PR reviewers can click straight from the comment
> to screenshots / video / trace.zip / network.har. Threaded via a new
> optional `spec_dir` param on `build_report`; v0.1 callers
> (`build_report` without `spec_dir`) stay backward-compatible with
> `evidence_urls_by_test_id={}`. Ordering is fixed (screenshots → video →
> trace → network → others by sorted key) so the report stays
> byte-identical for the same input. 7 new tests in
> `tests/test_triage_report.py` covering: empty-evidence v0.1 path,
> spec_dir walk wiring URLs into the report, markdown emoji bullets
> rendered, no-evidence-dir omitted, flagged-yes/rejected-no scope,
> deterministic ordering, ghost-test_id pruned.
>
> **Backend tests: ~1175 passing · Frontend tests: 49 (LaneStatusGrid)
> + 187 (PFactoryTaskDetail with evidence tab) + suite.**

### All 16 tasks closed

| Task | Issue | What shipped |
|------|-------|--------------|
| Task 0  | #16 | Lane spine rename (FUNCTIONAL→UNIT etc.) + frontend reskin |
| Task 1  | #17 | Framework descriptor registry (3 frameworks at MVP, ramp path to 80) |
| Task 2  | #18 | `.pfactory.yml` schema + parser + validator (Pydantic v2) |
| Task 3  | #19 | `.pfactory/tests-catalog.json` schema + atomic IO + 3-step lookup + migration primitive |
| Task 4  | #20 | Snapshotter extension (reads `.pfactory.yml` + tests-catalog into `context/`) |
| Task 5  | #21 | Planner polyglot subtask schema `(language, framework, target_name, intent)` |
| Task 6  | #22 | Gen-Functional generic prompt + per-framework `FrameworkDescriptor.context_block` injection |
| Task 7  | #23 | Per-framework Docker runner images (pytest / Jest / Playwright) + CI workflow |
| Task 8  | #24 | Browser-lane AppRuntime (docker-compose + HTTP HEAD health-poll) |
| Task 9  | #25 | Evaluator per-language primitives — TypeScript `preflight.py` (tsc) / `flake_lint.py` (ESLint) / `mutate_probe.py` (Stryker) |
| Task 10 | #26 | Evaluator coverage adapter (null vs zero for Browser lane per Decision 11) |
| Task 11 | #27 | Triager update-in-place vs create-new vs skip-locked via tests-catalog (3-step lookup) |
| Task 12 | #28 | 15 starter templates (5 each for Playwright / Jest / pytest) + `string.Template` engine |
| Task 13 | #29 | Skills + slash commands — `pfactory-init` / `pfactory-add-test` / `pfactory-from-template` + handover update |
| Task 14 | #30 | Portal endpoints — frameworks / templates / skills / catalog (FastAPI shim pattern) |
| Task 15 | #31 | LaneStatusGrid full reskin (5 independent lanes) + `pfactory init` / `pfactory migrate v0_1_catalog` CLIs |
| Task 16 | #32 | Test evidence capture (screenshots / video / trace / HAR) + retention enforcer + portal endpoint + frontend Evidence tab |

### Task 16 highlights

- **Test evidence capture** — every Browser-lane failure produces
  screenshots + video + trace.zip; every API/Integration test produces a
  network.har. Evidence stored at
  `spec_dir/findings/evidence/<test_id>/`, served via the portal, and
  rendered in a new **Evidence** tab in `PFactoryTaskDetail`.
- **Evidence retention enforcer** — prunes artefacts per configurable
  policy (failures: forever; flagged: 90 days; passing: 7 days; size cap
  per task). Fully injectable `now` parameter for deterministic tests.
- **CatalogEntry evidence fields** — `last_evidence_run_id` and
  `evidence_urls` (via `evidence_urls_raw` + `.evidence_urls` property)
  added to the test catalog; backward-compatible with pre-Task-16 catalogs.
- **EvidencePolicy** in `.pfactory.yml` — concrete typed sub-models for
  `browser:`, `api:`, and `retention:` blocks replace the freeform
  placeholder.
- **Portal evidence endpoint** —
  `GET /api/pfactory/tasks/{spec_id}/evidence/{test_id}/{artifact}` with
  content-type by extension and path-traversal rejection on all three
  path segments.
- **90 new backend tests** across 4 test modules; **8+ new frontend tests**
  in `PFactoryTaskDetail.test.tsx`.

---

## v0.2 — Enterprise Test Framework Spine (in progress)

> Successor to [v0.1.0-mvp](#v010-mvp--walking-skeleton-2026-05-28).
> v0.2 lights the **Browser** lane (Playwright via a docker-compose
> AppRuntime), adds the **API** + **Integration** lanes (HTTP/contract +
> testcontainers), and ships the **framework descriptor registry**,
> **`.pfactory.yml`** target schema, and **`.pfactory/tests-catalog.json`**
> cross-run continuity store. Planner becomes polyglot per-subtask
> (Python+TypeScript at MVP); Gen-Functional generalises via per-framework
> templates + context blocks; Evaluator gains TypeScript primitives
> (tsc / ESLint / Stryker); Triager learns update-in-place vs create-new
> via the catalog; evidence (screenshots / video / trace / HAR) is
> auto-captured and served by the portal for human review.
>
> Driver: `docs/plans/2026-05-28-enterprise-test-frameworks-design.md`
> (11 locked decisions, 80 frameworks catalogued).
> Task plan: `docs/plans/2026-05-28-enterprise-test-frameworks-tasks.md`
> (16 tasks, ~95 commits, ~975 tests at the v0.2 finish line).

### ⚠ BREAKING CHANGE — Lane spine rename (Task 0 / #16)

The `Lane` enum's vocabulary swapped from v0.1's pipeline-stage terms to
v0.2's **modality-based** decomposition (Decision 2):

```
v0.1                       →   v0.2
─────────────────────────────────────────
Lane.FUNCTIONAL            →   Lane.UNIT
Lane.SAST   (out of scope) →   Lane.BROWSER     (new — Playwright)
Lane.DAST   (out of scope) →   Lane.API         (new — HTTP/contract)
Lane.FUZZ   (out of scope) →   Lane.INTEGRATION (new — testcontainers)
Lane.MUTATION              →   Lane.MUTATION    (unchanged)
```

Why: the v0.1 lane names tracked the *security-pipeline* metaphor we
adopted from AIFactory. v0.2's brief (per the user's direction during
the `/super-brainstorm` interview that produced the design doc)
narrowed the product to **functional + feature testing** — security
scanning is owned by separate pipelines / controllers. The new lane
names describe the *modality* of the test (where it runs, what it
exercises), not the pipeline stage.

**Compatibility through v0.2 (removed in v0.3):**

- `Subtask.from_dict({"lane": "functional", ...})` still parses; the
  string is remapped to `Lane.UNIT` with a `DeprecationWarning`.
- `Subtask.from_dict({"lane": "sast", ...})` (and `"dast"`, `"fuzz"`)
  likewise remaps to `Lane.UNIT` with a warning.
- `lane_dispatch.dispatch_lane(lane="functional", ...)` lights the lane
  via the same DockerRunner path used by `"unit"`, also with a warning.
- `lang_registry.get_tool_for_lane(lang, "functional")` returns `None`
  (the registry is keyed on the new lane vocabulary; remap upstream).
- Workspaces created against v0.1 (`status.json["lane_progress"]`
  keyed on `"functional"`) remain readable — the MCP `task_status`
  surface returns the new `"unit"` key but old files lift correctly.

**Frontend:** `LaneStatusGrid` reskinned in lockstep — the lit lane
(was the FUNCTIONAL card showing the current task's status) is now
the **Unit** card; the four placeholder cards relabel to **Browser**
(Phase 2), **API** (Phase 3), **Integration** (Phase 4), **Mutation**
(Phase 5). The component's prop renamed: `functionalStatus` →
`unitStatus`. Full visual polish (icons + colors per lane) lands in
Task 15 (#31).

### Tasks shipped in v0.2

- **#16 Task 0**: Lane rename + breaking-change migration
- **#22 Task 6**: Gen-Functional refactor — generic prompt + context injection

  Gen-Functional is now polyglot. The v0.1 Python+pytest-specific prompt is
  preserved as `prompts/gen_functional-v01-legacy.md` (removed in v0.3). A
  new generic `prompts/gen_functional.md` is parameterized at runtime by the
  framework descriptor's `context_block`. The prompt helper
  (`get_pfactory_gen_functional_prompt`) accepts an optional
  `framework_descriptor` argument (pass `None` for the legacy path; a
  `DeprecationWarning` is emitted). The dispatcher (`gen_functional.py`)
  calls `framework_registry.load_registry()` per subtask, resolves the
  descriptor, and injects it into the prompt assembly. The runner image
  (`DockerRunner(image=...)`) is similarly parameterized by
  `descriptor.runtime.image` instead of the hardcoded
  `pfactory-runner-python:latest`. 25 new tests (57 total across the two
  gen_functional test files).

- **#30 Task 14**: Portal endpoints — framework registry, templates, skills, catalog

  Five new read-only FastAPI routes under `/api/pfactory/`:

  - `GET /api/pfactory/frameworks` — list all registered frameworks (name,
    language, lanes, coverage_strategy, version_range, template_count),
    sorted alphabetically.
  - `GET /api/pfactory/frameworks/{name}` — full `FrameworkDescriptor` as
    JSON. 404 for unknown names; 400 for path-traversal.
  - `GET /api/pfactory/templates?framework={name}` — list templates for a
    framework (name + metadata). 400 if param absent; 404 if framework
    unknown.
  - `GET /api/pfactory/templates/{framework}/{name}` — full template body +
    metadata. 404 if framework or template unknown; 400 for path-traversal
    on either segment.
  - `GET /api/pfactory/skills` — list `.claude/skills/*/SKILL.md` bundles
    with parsed YAML frontmatter. Gracefully returns `{"skills": []}` when
    the directory is absent (Task 13 may not yet be merged).
  - `GET /api/pfactory/tasks/{spec_id}/catalog` — serve the spec's
    `context/tests_catalog.json` snapshot verbatim. 404 if not snapshotted.

  All routes follow the FastAPI-shim-friendly pattern from v0.1's
  `pfactory_tasks.py` (no hard fastapi import; route functions are unit-
  testable without fastapi installed). 127 new tests across four test
  files (`test_pfactory_routes_frameworks.py` · `test_pfactory_routes_
  templates.py` · `test_pfactory_routes_skills.py` + 5 new /catalog
  cases in `test_pfactory_routes_tasks.py`).

- **#31 Task 15**: LaneStatusGrid full reskin + `pfactory init` + `pfactory migrate` CLIs

  LaneStatusGrid receives a complete reskin: all five lane cards (Unit /
  Browser / API / Integration / Mutation) are independently lit by
  `laneStatuses: Partial<Record<LaneId, string|null>>`. Each lane has a
  unique icon (CheckSquare / Globe / Plug / Network / Zap) and accent
  colour (blue / purple / green / orange / red). `PFactoryTaskDetail`
  derives per-lane statuses from `status_json.lane_progress` with v0.1
  compat fallback. `unitLaneState` kept as alias.

  Two new backend CLI commands:
  - `python -m cli init` — interactive scaffolder for `.pfactory.yml` +
    empty `.pfactory/tests-catalog.json`. Non-interactive mode testable
    via flags. Validates output via `load_pfactory_yml()`.
  - `python -m cli migrate v0_1_catalog` — walks
    `~/.pfactory/workspaces/*/specs/*/` and consolidates per-spec test
    entries into per-repo `.pfactory/tests-catalog.json` via the
    existing `tests_catalog.migration.migrate_v0_1_workspace` primitive.
    Dry-run flag prints the plan without writing.

  21 new backend tests (`test_cli_init.py` + `test_cli_migrate.py`);
  23 new frontend LaneStatusGrid tests (49 total in the file).

---

## v0.1.0-mvp — Walking Skeleton (2026-05-28)

> First tagged release. The MVP walking-skeleton pipeline is complete:
> a Claude Code session in an AIFactory repo can invoke
> `/handover-to-pfactory` and the four-agent pipeline (Planner →
> Gen-Functional → Evaluator → Triager) produces a pytest test suite +
> verdicts + a triage report against the AIFactory feature branch.
>
> Functional lane (Python) is active; SAST / DAST / Fuzz / Mutation
> lanes are Phase 2-5 placeholders in the portal.
>
> Tests: **531 backend + 112 frontend = 643 total**, plus a 9-scenario
> manual end-to-end smoke runner (`scripts/e2e-smoke.sh`). Side-effects
> (git commit + PR comment) default to dry-run per the
> "no automatic pushes" policy.

### Tasks shipped (all 12)

  - **#2  Task 1**: Hard fork from AIFactory + scaffold
  - **#3  Task 2**: MCP server + `/handover-to-pfactory` skill
  - **#4  Task 3**: Workspace + snapshotter
  - **#5  Task 4**: Docker runner + lane dispatcher + lang registry
  - **#6  Task 5**: Planner agent (initial + replan + stuck-at-2 transition)
  - **#7  Task 6**: Gen-Functional agent (pre-flight static check +
                    flake-risk lint guardrails)
  - **#8  Task 7**: Evaluator (5-signal verdict pipeline:
                    coverage delta · 3× stability · mutate-and-check ·
                    flake-lint promotion · LLM semantic relevance)
  - **#9  Task 8**: Triager (dedup + rank + report + git_writer +
                    pr_comment, all dry-run by default)
  - **#10 Task 9**: Portal backend retheme (FastAPI `/api/pfactory/tasks`
                    + 5 artefact endpoints + WebSocket log stream)
  - **#11 Task 10**: Portal frontend retheme (React lane status grid,
                    task list, detail view, log viewer, shell)
  - **#12 Task 11**: End-to-end smoke (9 verification scenarios + bash
                    orchestrator + structural test harness +
                    operator guide)
  - **#13 Task 12**: Documentation + tag v0.1.0-mvp (this release)

### Pipeline architecture

```
  Planner ─► Gen-Functional ─► Executor ─► Evaluator ─► Triager
   (#6)        (#7)              (#5)        (#8)        (#9)
```

Each agent's success path schedules the next via env-gated async tasks
(`PFACTORY_AUTO_PLAN`/`GENERATE`/`EVALUATE`/`TRIAGE`, all default ON).
Gen-Functional → Planner replan loops when a guardrail rejects, capped
by `replan_count >= 2` → `status=stuck`.

### Test surface

  | Suite | Cases | Run cmd |
  |---|---:|---|
  | Backend non-SDK | 531 | `pytest -q tests/` |
  | Frontend (vitest + RTL) | 112 | `cd apps/frontend-web && vitest run` |
  | E2e smoke (manual) | 9 scenarios | `scripts/e2e-smoke.sh --all` |

Every Claude SDK + Docker call is mocked at a seam so CI runs in seconds
without API keys or daemons. The e2e smoke exercises the real stack and
is operator-driven.

### Workspace layout

Per-task state lives at `~/.pfactory/workspaces/<proj>/specs/<spec>/`:

  - `status.json` — live status / phase / counts
  - `test_plan.json` — Planner's lane-tagged subtasks
  - `context/` — frozen AIFactory spec + diff + replan_request.json
  - `tests/` — Gen-Functional's generated pytest files
  - `findings/` — verdicts.json + triage_report.{md,json} + mutants/
  - `logs/` — per-agent transcripts

### Triager dry-run defaults

Per CLAUDE.md "no automatic pushes" policy, the Triager's git_writer
and pr_comment helpers default to dry-run (record the argvs they
WOULD invoke without executing). Operators opt in via env:

  - `PFACTORY_TRIAGER_GIT_WRITE=1` — actually commit accepted tests
  - `PFACTORY_TRIAGER_PR_COMMENT=1` — actually `gh pr comment`

### Deferred for v0.1.1+ (see #14)

  - **AIFactory `runners/github/` trim** (sub-task 8.4): ~21,600 LOC
    inherited PR-review machinery. Web-server still consumes parts;
    needs careful surgery.
  - **AIFactory spec-creation routes trim** (sub-task 9.2): inherited
    FastAPI endpoints we don't use.
  - **Inherited React spec wizard UI trim** (sub-task 10.2): 355
    TS/TSX files; PFactory components live alongside cleanly.
  - **Live log streaming**: WS sends ONE snapshot on connect at MVP;
    live tail-as-file-grows is Phase 2.
  - **Per-test coverage XML wiring**: `coverage_delta` primitive is
    fully tested but inputs aren't wired yet — degrades to
    "not computed".
  - **`source.json` PR + repo_slug fields**: snapshotter doesn't
    populate yet — operator sets them manually for now.

### Known sharp edges

See `guides/e2e-smoke.md` "Phase-2 backlog" for the full catalogue.
Highlights:

  - Verification field schema drift (`planner.md` emits `"command"`;
    dataclass has `run`). Both shapes accepted via duck-typing.
  - Manual smokes 6/8/9 lack auto-assertions (intentional —
    require docker/gh state changes).
  - NixOS devShell sets `NODE_ENV=production`; must `unset NODE_ENV`
    before `npm install` in `apps/frontend-web/`.

### Documentation

  - `README.md` — front door + quickstart
  - `CLAUDE.md` — guidance for Claude Code sessions inside this repo
  - `guides/e2e-smoke.md` — 9-scenario operator walkthrough
  - `guides/handover-to-pfactory-skill.md` — companion-skill reference
  - `guides/HANDOVER_WORKFLOW.md` — operator handover flow
  - `guides/CLAUDE_CODE_MCP_TOOLS.md` — MCP tool reference

---

## Unreleased

### ⚖️ Licensing

- **Relicensed from AGPL-3.0 → dual MIT OR GPL-3.0.** PFactory is now
  available under the recipient's choice of either license. See
  `LICENSE`, `LICENSE-MIT`, and `LICENSE-GPL`. SPDX identifier:
  `MIT OR GPL-3.0-only`. The `dataseek.team` enterprise-licensing
  contact line (which referenced a non-existent email) was removed.

### 🏷️ Branding

- **Rebrand `dataseeek` → `olafkfreund`.** The `dataseeek` GitHub org
  doesn't exist; every reference in non-archive files was rewritten to
  point at the actual repo location (`olafkfreund/PFactory`) and the
  actual GitHub Pages URL (`olafkfreund.github.io/PFactory`). Affects
  README badges, docusaurus config, package.json URLs, demo repo path,
  cosign verify identity in image-mirroring drills, and ghcr.io image
  paths in the Helm chart docs.

### 📚 Documentation

- **Full docs rewrite + GitHub Pages site.** The `guides/` directory was
  archived to `docs-archive/2026-05-26/guides/` (git history preserved).
  A fresh Docusaurus site at `docs/` is published to
  <https://olafkfreund.github.io/PFactory/> via a new
  `.github/workflows/docs.yml` workflow. Includes 18 reorganized pages:
  Getting Started, Demo, Concepts (3), Architecture (3 with Mermaid
  diagrams), Wiki (FAQ/Troubleshooting/Glossary), Showcase, Compliance
  (SOC2/GDPR), Contributing, Roadmap. The legacy `guides/` content is
  unchanged in archive form and still searchable via `git log --follow`.

- **README.md slimmed from 557 to 115 lines.** Hero + tagline + 60-second
  quickstart + demo callout + screenshot grid + prominent docs links.
  Everything operational moved to the docs site.

### ✨ Added

- **`scripts/demo.sh`** — end-to-end demo runner (Bash + jq + gh).
  Seeds `olafkfreund/pfactory-demo` with 3 issues, registers the repo
  with your portal, imports the issues as backlog tasks, prompts you
  to drive Claude Code from the terminal, then kicks off an autonomous
  build. Flags: `--yolo`, `--no-reset`, `--portal=URL`.

- **`scripts/capture-screenshots.ts`** — Playwright headless Chromium
  driver that captures 14 named PNGs of the marquee portal views to
  `docs/static/img/screenshots/`. Reproducible — anyone can refresh
  the gallery with `npm -w apps/frontend-web run capture-screenshots`.

- **`Justfile`** — canonical command index. `just --list` shows
  `install`, `backend`, `frontend`, `docs-dev`, `demo`, `screenshots`,
  `test-backend`, `test-frontend`, `test-postgres`, `test-all`.

- **Root `package.json` scripts**: `docs:install`, `docs:dev`,
  `docs:build`, `demo`, `screenshots`.

---

## 3.0.2 - 2026-05-26

Patch release fixing two leftover wiring + branding bugs from v3.0.0.

### 🛠️ Fixed

- **P6 observability never wired into `main.py`**. The
  `server/observability/` package shipped in v3.0.0 (Epic #26 P6)
  but `main.create_app()` never called `install_metrics(app)`,
  `configure_structlog()`, or `app.add_middleware(CorrelationIdMiddleware)`.
  As a result the production portal exposed neither `/metrics` nor
  structured JSON logs nor correlation IDs — despite all P6 unit
  tests passing (they built their own minimal FastAPI app and called
  the functions directly, bypassing main.py). v3.0.2 wires the three
  calls in the correct order:
  - `configure_structlog()` at the top of `create_app()` so
    boot-time logs are already JSON.
  - `CorrelationIdMiddleware` added LAST so it's the outermost
    layer (sets X-Request-ID before TokenAuth runs; 401 responses
    still carry the ID — auditors rely on this).
  - `install_metrics(app)` after all routers are mounted so the
    Prometheus instrumentator can derive cardinality-capped
    `handler` labels from the route table.

  Regression test added at `tests/obs/test_p6_main_wiring.py`:
  imports `main.create_app()` and asserts `/metrics` returns 200 +
  CorrelationIdMiddleware echoes back `X-Request-ID` + the FastAPI
  app title is PFactory + `app.version` matches the package version.
  Gates every PR forward.

- **Leftover Magestic branding in `main.py`**. The v3.0.0 rebrand
  missed three string constants:
  - `title="Magestic AI Web API"` → `"PFactory Web API"`
  - `description="Web API for Magestic AI autonomous coding framework"`
    → `"Web API for PFactory — self-hosted AI task management +
    agent orchestration"`
  - Root-route message `"Magestic AI Web Server"` →
    `"PFactory Web Server"`

  Plus the hardcoded `version="1.0.0"` on the FastAPI app + on
  `/api/health` was a drift hazard. v3.0.2 reads the canonical
  version from `apps/backend/__init__.py` at startup (the file
  `bump-version.js` already updates on every release), via a tiny
  `_read_app_version()` helper. No more silent version-skew.

### Upgrade notes

- Backwards-compatible patch: `helm upgrade pfactory --version 3.0.2`
  picks up both fixes with no schema or config changes.
- Operators who deployed v3.0.1 had a non-functional `/metrics`
  endpoint. After upgrading, configure your Prometheus scrape job
  against the now-live endpoint (see `docs-archive/2026-05-26/guides/operations/observability.md`).

## 3.0.1 - 2026-05-26

Patch release with two operator-visible fixes.

### 🛠️ Fixed

- **SQLite migration crash on fresh install**. The P2.3
  `encrypt_credentials` migration (`c6e3b2d4a8f0`) used a direct
  `op.alter_column(nullable=False)` to re-apply the NOT NULL
  constraint on `email_accounts.access_token` after the encrypted-
  column swap. SQLite doesn't support `ALTER TABLE ... ALTER
  COLUMN ... SET NOT NULL` — backends booting against a fresh
  SQLite (`autoApply=true` in the Helm chart's POC path; default
  local-dev path) crashed during `alembic upgrade head`. Wrapped
  the step in `op.batch_alter_table`, mirroring P3.3's
  `d8f1a3c5e7b9` migration. Postgres deployments are unaffected
  (their behavior was correct via the same native ALTER).
  Regression test added at `tests/secrets/test_p2_sqlite_migration.py`
  that runs `alembic upgrade head` against a temp SQLite file —
  gates every PR going forward.

- **PFactory logo not displaying in the sidebar/loading screen/
  onboarding**. The new logo + favicon assets were stashed before
  P1 work began and never restored to the main release. Bundle
  contains the updated `logo.png` (547 KB, full-res PFactory
  brand), `favicon.ico` (15 KB), `apple-touch-icon.png` (43 KB),
  and 16/32 px favicon variants. The sidebar `<img src="/logo.png">`
  reference is unchanged — the new files just slot in.

### Upgrade notes

- **Operators on v3.0.0**: this is a backwards-compatible patch.
  `helm upgrade` to v3.0.1 picks up both fixes.
- **Operators who already migrated** (the SQLite migration crash
  blocked them from getting that far on v3.0.0): no special
  handling needed — fresh install + `helm install pfactory --version 3.0.1`
  works end-to-end.

## 3.0.0 - 2026-05-26

The PFactory **enterprise GA** release (Epic #26). Self-hosted Helm
chart with PSS-restricted defaults, encrypted-at-rest secrets backed
by 5 KMS backends, OIDC SSO, tamper-evident audit chain, GDPR
right-to-erasure, structured-JSON observability + Prometheus
metrics, and a full SOC 2 / GDPR / STRIDE evidence pack with three
ship-readiness drill scripts.

### ⚠ Breaking changes

- **Forward-only schema migration** `c6e3b2d4a8f0_encrypt_credentials`:
  `email_accounts.access_token`, `email_accounts.refresh_token`, and
  `llm_endpoints.api_key` columns convert from plaintext `Text` to
  encrypted `LargeBinary`. The migration is **forward-only** — there
  is no downgrade path. Operators MUST take a `pg_dump` backup before
  upgrading from any v2.x install.
- **Required Postgres backend for production**: SQLite remains
  supported for dev/POC, but `kms_data_keys` + the audit chain
  expect Postgres semantics for indexed lookups.
- **Container runs as non-root uid 65532** with read-only root
  filesystem and dropped capabilities. Operators with custom
  init-containers writing to `/` must mount tmpfs/emptyDir.

### ✨ Added — Epic #26 phases

- **P0 — Container hygiene**: Chainguard distroless base
  (digest-pinned), Trivy CVE scan, Syft SBOM, cosign keyless
  signing via GitHub OIDC, multi-arch (amd64+arm64) manifest
  inspection.
- **P1 — Postgres backend**: `asyncpg` driver, Alembic migrations,
  optional `APP_MIGRATIONS_AUTO_APPLY=false` for Helm Job mode,
  bank-grade privilege model (no SUPERUSER, no CREATE EXTENSION).
- **P2 — Encrypted secrets at rest**: `EncryptedString`
  `TypeDecorator` over `LargeBinary`, per-org `kms_data_keys` with
  LRU cache, 5 KMS backends (`fernet` for dev, `aws_kms`,
  `vault_transit`, `azure_kv`, `gcp_kms`), root-key rotation CLI
  (`python -m server.crypto rotate-root`), forward-only column
  migration with KMS-aware backfill.
- **P3 — OIDC SSO**: `authlib`-based Authorization Code + PKCE +
  state + nonce, JIT user/`OrganizationMember` provisioning with
  claim-mapped roles (`APP_OIDC_GROUP_TO_ROLE`), 15-minute access
  TTL + 8-hour refresh, IdP-validated refresh path with userinfo
  caching, logout redirect to IdP `end_session_endpoint`. Presets
  for Keycloak, Okta, Azure AD.
- **P4 — Helm chart**: `charts/pfactory/` with PSS-restricted
  security contexts, default-deny NetworkPolicy + 443 egress
  allowlist, ExternalSecret templates for 4 backends, optional
  bundled Postgres `StatefulSet` for POC mode, `customCABundle`
  for TLS-intercepting corporate proxies, schema-validated
  `values.yaml`.
- **P5 — Audit hardening**: SHA-256 hash chain on every audit-log
  write, NDJSON + CSV streaming export at `/api/audit/export`,
  air-gappable external verifier (`python -m server.audit
  verify-chain`), GDPR Art. 17 erasure that re-hashes the chain so
  `verify-chain` continues to pass, daily retention job (default
  13 months = SOC 2 12 + buffer).
- **P6 — Observability**: `structlog` JSON-to-stdout with
  ISO-8601 timestamps + `request_id` binding, correlation-ID
  middleware (`X-Request-ID`) with `httpx` propagation, Prometheus
  `/metrics` with cardinality-capped `handler` labels (route
  templates, not raw paths), optional `METRICS_SCRAPE_TOKEN`
  bearer gate, Helm `ServiceMonitor` template, pre-built Grafana
  dashboard JSON (7 panels).
- **P7 — Evidence + ship-readiness drills**: SOC 2 evidence pack
  (CC1-CC9 + A1 + C1), GDPR DPIA + data-flow diagram, STRIDE
  threat model, 4-cloud-path deployment runbook (EKS+RDS / AKS+
  Azure Postgres / GKE+Cloud SQL / vanilla K8s+Vault), v0.x → v3.0
  upgrade guide, three executable drill scripts
  (`backup-restore.sh`, `upgrade-in-place.sh`, `image-mirroring.sh`)
  with `--dry-run` modes.

### 📚 Documentation

New operator runbooks under `guides/`:
- `guides/operations/audit-trail.md`
- `guides/operations/encrypted-secrets-dr.md`
- `guides/operations/image-mirroring.md`
- `guides/operations/kms-rotation-runbook.md`
- `guides/operations/observability.md`
- `guides/operations/oidc-setup.md`
- `guides/deployment/helm-install.md`
- `guides/deployment/runbook.md`
- `guides/deployment/upgrade.md`
- `guides/compliance/soc2-evidence.md`
- `guides/compliance/dpia-data-flow.md`
- `guides/security/threat-model.md`
- `guides/observability/grafana-pfactory.json`

### 🧪 CI

11 acceptance jobs gate every PR (≈2000 tests total):
`backend (ruff + pytest)`, `docker (P0)`, `postgres (P1) × {15, 16}`,
`secrets (P2)`, `oidc (P3)`, `helm (P4)`, `audit (P5)`, `obs (P6)`,
`evidence (P7)`, `frontend (typecheck)`.

### ⚠ Documented v3.0 limitations (v3.1 follow-ups)

Tracked in `guides/compliance/soc2-evidence.md § Documented
limitations`. Each maps to a v3.1 Epic #35 issue:

1. Audit chain has no signed external anchor.
2. Revocation latency bounded by 15-minute access-token TTL (back-
   channel logout deferred).
3. FIPS 140-2/3 modules not validated.
4. No built-in OpenTelemetry distributed tracing.
5. Single-replica only (multi-replica via Redis pub/sub deferred).
6. LLM-call audit deferred to v3.1 LiteLLM gateway.

### ✨ Added
- **GitHub PR Review Integration**: End-to-end support for PR reviews including listing, fetching, posting reviews, checking new commits, and viewing logs via dedicated API endpoints.
- **PR Review WebSocket Events**: Real-time progress, completion, and error events via WebSocket for live feedback during PR reviews.
- **PR Action Endpoints**: Support for posting reviews, commenting, merging, assigning, and canceling PRs through backend API.
- **AI-Powered Conflict Resolution**: Enhanced "Fix Conflicts with AI" functionality with real git merge and AI resolution of conflict markers.
- **Task from Chat Feature**: Button in Insights chat to convert conversation into a structured task (title + PRD description) with editable preview.
- **Open in Browser**: New "Open in Browser" button in EditorPage that serves files with correct MIME types and asset URL rewriting.
- **QA Fixer Phase**: Added separate `qa_fixer` phase in phase configuration, allowing independent model and thinking settings.
- **Phase-Scaled Progress**: Monotonically increasing progress percentages across phases (planning 0–20%, coding 20–80%, QA 80–95%, complete 95–100%).
- **Terminal Persistence**: TerminalGrid now remains mounted across view switches to prevent stuck terminals and lost PTY connections.
- **Model & Token Metrics**: Display assistant model name on chat messages and show tokens/sec metrics after each response across all providers.
- **Dark Theme & UI Improvements**: Enhanced folder navigation, keyboard support (Enter/Backspace), HTML preview, progress labels, and overall dark theme consistency.

### 🛠️ Fixed
- **GitHub PR Connection Detection**: Fixed incorrect endpoint call (`window.API.github.checkGitHubConnection` → `window.API.checkGitHubConnection`).
- **AI Merge Conflict Resolution**: Fixed syntax error in `github.py` caused by AI-generated extra closing brace.
- **requireReviewBeforeCoding Sync**: Ensured field is written to `task_metadata.json` when editing tasks.
- **Email Notifications**: Fixed silent failure under legacy token auth by populating default user context.
- **Build Progress & Subtask Status**: Added fallback in `post_session_processing` to detect new commits and force-update status.
- **File Serving 404s**: Resolved `404` errors for `/api/files/serve` by properly staging the endpoint and enabling public access with path-traversal protection.
- **Model Config Loss**: Fixed `UpdateModelConfigRequest` to preserve all fields (provider, profileId, model, thinkingLevel, temperature).
- **Issue-to-Task Creation**: Fixed backend `TaskMetadata` model to include `githubIssueNumber`, `affectedFiles`, and `acceptanceCriteria`.
- **Sidebar Layout**: Restored proper layout and spacing in sidebar components.

### 🔧 Changed
- **Project Renaming**: Renamed from "Claude Code Manager Web" to **PFactory** across UI, navigation, and documentation.
- **MCP Template Filtering**: Removed redundant and duplicate quick templates (filesystem, fetch, github, gitlab) that conflict with native tools.
- **Hardcoded Model Values**: Replaced inline model/thinking defaults with shared constants to ensure user-configured settings take effect.
- **Git Ignore Safety**: Added `.pfactory-security.json` and `.pfactory-status` to `.gitignore` during project init and unstage during merges.
- **CLI Detection Optimization**: Improved speed using `shutil.which` and `npm package.json` parsing instead of slow Node.js startup (~4s → <50ms).

### 📦 Updated
- **README.md**: Updated project documentation with fixed GitHub URL, removed non-existent files, and added Docker deployment guide.
- **Phase Progress Logic**: Refactored progress logic to prevent backward jumps between phases using defined phase ranges.