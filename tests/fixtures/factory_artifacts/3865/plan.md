---
status: approved
issue: 3865
spec: spec/2026-11-23-3865-factory-cli.md
coders:
  1: agy      # Factory schema fields + validation test: mechanical
  2: codex    # PFactory mapper: small, tricky parsing rules
  3: codex    # PFactory route, claim, emit guards, attempt record: security/state logic
  4: codex    # TFactory no_register + reasons + vendored schema: small, touches authz
  5: codex    # AIFactory idempotent from-plan + provenance tests: small, state logic
  6: agy      # CLI package: large, specified below in full
  7: agy      # vendoring, flake, uv, CI wiring: mechanical
reviewers:
  - codex    # reviews the agy-implemented PRs (steps 1, 6, 7) read-only
  - agy      # reviews the codex-implemented PRs (steps 2-5) read-only
---

# Plan: `factory` CLI and MCP front door, release 1 (#3865)

Self-contained: every approved spec decision (D1-D6 and amendments A1-A10)
is restated here. The lead (Claude, Opus) reviews every diff against this
plan with a fresh Opus reviewer as well; implementers never commit.

## Decisions

### Keys and URLs (D2, A7)

- URL per portal: `<P>_API_URL`, else `~/.config/factory/config.toml`
  `[portals.<p>] url`, else default
  `https://{pfactory,aifactory,tfactory}.freundcloud.org.uk`,
  `https://cfactory-mcp.freundcloud.org.uk`. The config file holds URLs only.
- Key: `$<P>_MCP_KEY` → `~/.<p>/.mcp-key` → `$<P>_API_TOKEN_FILE` →
  `~/.<p>/.token`; CFactory `$CFACTORY_API_KEY` → `~/.cfactory/.mcp-key` →
  `~/.cfactory/.token`. Owner's `.token` files are user `acw_` keys (live
  check 2026-11-23).
- Routes: PFactory/AIFactory/TFactory only under `/api/mcp-stdio/*`;
  CFactory `/api/workitems/*`, `/health`.
- Transport: https or loopback http; reject URL userinfo; no redirects
  (`factory_common` no-redirect opener); redact the loaded key value and
  secret patterns from every error, warning and MCP result.
- `factory doctor`: per portal URL, key source name, key kind (`acw_` or
  legacy), health version (`/api/health`, CFactory `/health`), protected
  read (project list; CFactory work-item list) → ok/denied. States that a
  legacy key can only dry-run submit, and that a CFactory key sees the whole
  tenant.

### `factory submit` (D3, A2)

CLI:
- `factory submit <slug | plan/<slug>.md> [--base REF] [--project ID] [--dry-run]`.
- Preconditions (exit 2 naming the problem): all three files exist,
  committed, frontmatter `status: approved`, working copy equals HEAD; per
  stage an approval commit that is an ancestor of HEAD, changes that file's
  frontmatter `draft`→`approved`, and after which the blob is unchanged to
  HEAD; SHA-1 repo (SHA-256 → exit 2).
- Identity: `origin` normalized to `provider` + `owner/repo`; current branch;
  HEAD SHA; `--base` default the remote's default branch.
- Calls: `POST /api/mcp-stdio/plan/sessions/ingest-approved` →
  `POST /api/mcp-stdio/plan/sessions/{id}/approve` →
  `POST /api/mcp-stdio/plan/sessions/{id}/emit-contract` (`dry_run` per flag).
- Output always: session id, correlation key (session's, which exists from
  creation as `pf-<session>` until an issue key replaces it); on success
  also the AIFactory task id. `--dry-run`: child list (title + kind) and
  contract summary, no downstream POST.
- Exit: 0 `contract_result.ok`; 1 portal 4xx (safe `detail`); 2 local
  precondition; 3 `unknown` (timeout, disconnect, 5xx after send, or
  PFactory `outcome="unknown"`). A re-run is safe (A2), but the CLI itself
  never loops on emit.

PFactory request (exact, `extra=forbid`):
```json
{"slug": "2026-11-23-3865-factory-cli", "provider": "github",
 "repo": "owner/repo", "base_ref": "main", "branch": "feat/x",
 "head_sha": "<40 hex>", "project_id": null,
 "artifacts": [{"stage": "intent|spec|plan", "path": "intent/<slug>.md",
   "content": "<utf-8 text>", "blob_sha": "<40 hex>",
   "approve_commit_sha": "<40 hex>", "approve_commit_author": "Name <e>"}]}
```
Bounds: SHAs `^[0-9a-f]{40}$`; author printable, ≤ 200, no control chars;
path repo-relative, ≤ 512, no `..`; exactly one artifact per stage.
Response `200 {"session_id", "correlation_key", "status", "deduped": bool,
"children": [{"key","title","kind"}], "readiness": {"hard_failures": [...]}}`.

### `factory test` (D4, A10)

- `factory test --project ID (--spec SLUG | --criteria FILE) --branch B [--pr N]`.
- Criteria: bullets of `spec/<slug>.md` `## Verification`, or of FILE;
  rewritten as `## Acceptance Criteria` with `- AC1: …`; none → exit 2.
- Run id: `<slug|file-stem>-<short sha>-<6 random hex>`, `[a-z0-9-]`, ≤ 64.
- `POST /api/mcp-stdio/specs/ingest` with `project_id, spec_id, spec_text,
  source_branch, repo, pr_number, no_register: true`.
- Prints `workspace_project_id`, `spec_id`, the branch head SHA it saw and
  TFactory's checked-out SHA (mismatch → warning), and
  `planner_reason` when `planner_scheduled` is false.
- Unknown or invisible project → "project not found", exit 1 (identical).

### `factory status` (D5)

- `pf:<session>` → `GET /api/mcp-stdio/plan/sessions/{id}`, then follow
  `contract_result` to AIFactory `GET /api/mcp-stdio/tasks/{task_id}`.
- `af:<task>` → AIFactory `GET /api/mcp-stdio/tasks/{task_id}`.
- `tf:<project>/<spec>` → `GET /api/mcp-stdio/spec-tasks/{project}/{spec}`.
- Bare correlation key → CFactory `GET /api/workitems/{key}` only with a
  CFactory key; none, 401 or 403 → exit 2 "use pf:/af:/tf: ids".
- Any 404 → "not found".

### `factory mcp`, `mcp-config` (D6, A6)

- Tools `factory_submit` (default `dry_run=true`), `factory_test`,
  `factory_status`, `factory_doctor`; same handlers as the CLI; JSON with
  `outcome: ok|rejected|unknown` and the same ids.
- `mcp-config claude|codex|agy` prints a secret-free entry (command
  `factory`, args `["mcp"]`): Claude `.mcp.json` `mcpServers.factory`;
  Codex `~/.codex/config.toml` `[mcp_servers.factory]`; agy
  `~/.gemini/config/mcp_config.json` `mcpServers.factory`.
- `--write`: only the `factory` entry; existing entry refused without
  `--force`. JSON: parse, set key, dump. Codex TOML: absent → append block;
  `--force` → replace `[mcp_servers.factory]` and `[mcp_servers.factory.*]`
  tables textually; always validate the result with `tomllib` before
  writing. Backup `.bak` mode 0600; write temp + `os.replace`; keep the
  original mode.

### PFactory server (D3, A1-A4, A8)

- Mapper `plan/ingest/artifacts.py`: NormalizedPlan title = intent H1;
  description = intent `## Problem` + `## Proposed outcome`; criteria = spec
  `## Verification` bullets (top-level bullets; nested bullets join their
  parent); `content_trust="untrusted_user_content"`;
  `source_format="factory-artifacts"`; `raw_text` = the three files joined
  (so the injection scan sees all of them). EpicPlan: one ChildIssue per
  plan `## Steps` numbered item (continuation lines included), order kept,
  `depends_on` previous; `kind` `task`, or `testing`/`cicd` when the step
  title names tests or CI (A1); body = step text verbatim; all spec criteria
  verbatim on the last child's `acceptance_criteria`; plan `## Tests` and
  `## Rollback` appended to the last child's body (child bodies reach
  AIFactory; `epic_body` does not, `contract_builder.py:105-124,250-258`);
  `epic_body` = full plan text. No steps → `ArtifactIngestError` → 422.
- Route: registered-project resolution (A3), never `for_intake`; then
  claim; then blob check (git framing over exact UTF-8 bytes) → 422 on
  mismatch; then the non-authoring part of `process()` on the existing
  off-loop admission path: classification, grounding, injection scan
  (flagged → hard tier as usual), review runner, `run_gates`,
  `annotate_plan`, persist the `PlanReview`, `_save`. Skip `_decompose` and
  `synthesize`. Hard failures block approve/emit; nothing auto-waived.
- `PlanSession.source_artifacts: list[dict] | None`; `submission_id`.
  `update_plan`, `apply_suggestions` and `process` raise 409 "artifact
  session: re-submit instead" for such sessions (guard in `PlanService`).
- Approve: `approver_display` resolved in the router from the authenticated
  user record and stored on `HumanApproval`; re-approve of an emitted
  artifact session → 409.
- Emit (artifact sessions, in `PlanService.emit_contract()` which holds the
  lease): `require_user_approval=True`; `require_signed=True` (A4);
  `allow_fallback=False`; repo and project from the session, body overrides
  ignored in both the router and the service; missing project → 422 (today
  `service.py:3094` falls back to `plan_id`); attempt record written before
  the POST and updated after (A2); already `ok` → return stored result, no
  POST; readback mismatch never re-POSTs. Stage-machine guards unchanged.
- Contract: `provenance.source_artifacts` (items add `attested_by:
  "client"`), `provenance.human_approval` (`user_id` from the authenticated
  key, `approver_display`, `auth_kind`, `approved_at`, `plan_hash`),
  `provenance.submission_id` (A2); added before both HMAC and Ed25519
  signing; `approval.approved_by` stays `"pfactory"`.
- Scoped `ingest-text` proxy catches `ValidationError` → 422 (A8).

### AIFactory (A2)

- `from-plan` idempotent on `(project, provenance.submission_id)` when the
  field is present: repeat → 200 with the existing task/spec id, no new
  spec. Absent → today's behaviour.

### TFactory (D4)

- `SpecIngestRequest.no_register: bool = False`; when true, unknown and
  invisible projects get the identical `404 {"detail": "Project not found"}`
  in both the scoped proxy and the shared registration branch, and nothing
  is registered.
- `planner_reason ∈ {checkout_failed, scheduler_failed, auto_plan_disabled}`
  whenever `planner_scheduled` is false.

## Merge order

TFactory (step 4, vendored schema first) → Factory schema PR (step 1) →
PFactory (steps 2-3, schema copy + `HUB_SHA`) and AIFactory (step 5) →
releases → Factory CLI PR (steps 6-7) → live acceptance (step 8). Step 6
can be implemented in parallel against the shapes pinned above.

## Steps

Worktrees under the session scratchpad. Factory base `main`; products base
`dev`. Branches: Factory schema `feat/3865-contract-provenance`, Factory CLI
`feat/3865-factory-cli` (this branch), products `feat/3865-<short>`.
Product ratchet: `apps/backend/.venv/bin/python scripts/ratchet_lint.py --base origin/dev --package apps/backend --package apps/web-server --package scripts`.

1. **Factory schema** (`feat/3865-contract-provenance`).
   `apis/task-contract.schema.json` `provenance` (`:19`): optional
   `source_artifacts` (array of `{stage enum, path, blob_sha
   ^[0-9a-f]{40}$, approve_commit_sha same, approve_commit_author
   maxLength 200, attested_by const "client"}`), `human_approval`
   (`{user_id, approver_display, auth_kind, approved_at date-time,
   plan_hash}`), `submission_id` (`^[0-9a-f]{64}$`); keep
   `additionalProperties: true`. Extend `scripts/validate_task_contract.py`
   checks and add `tests/test_task_contract_provenance.py` (valid with and
   without the fields; bad SHA rejected).
   → verify `pytest tests/ shared/factory-common/tests/ -q` and
   `python scripts/validate_task_contract.py`.
   Traps: no regeneration (A5); merge only after step 4's TFactory PR.

2. **PFactory mapper** (`feat/3865-ingest-approved`).
   `apps/backend/plan/ingest/artifacts.py` per the mapper decision, modelled
   on `plan/ingest/speckit.py` (`ingest_speckit` `:342`, `_epic_from_tasks`
   `:298`, `_section_lines` `:229`). Tests `tests/test_ingest_artifacts.py`
   with fixtures copied from real triples (this one and
   `2026-11-19-3858-factory-ui-breadcrumb` from Factory `main`).
   → verify `apps/backend/.venv/bin/pytest tests/test_ingest_artifacts.py -q` + ratchet.
   Traps: strip frontmatter first; `ChildKind` values (A1); mypy strict.

3. **PFactory route, claim, emit** (same PR as 2).
   - Route in `apps/web-server/server/mcp_stdio/router.py` next to
     `ingest-text` (`:589-608`), `_PlanWrite` (`:538-540`); fix the
     `ingest-text` `IngestTextBody(**body)` 500 → 422 (A8).
   - `PlanService.ingest_approved()` in `apps/backend/plan/service.py`:
     registered-project resolution (A3); claim per `from-issue`
     (`:1097-1161`: `store.upsert(claim_id, payload="{}", seq=0,
     tenant_id=…, expected_version=0)`, `acquire_emit_lease` retry, recheck,
     `delete_if_lease_owner` in `finally`, `self._emit_locks` for the
     store-less path); extract the non-authoring stages from `process()`
     (`:1446-1517`, stages `:1473-1495`, classification/scan `:1708-1754`,
     admission `:1583-1615`) into a helper used by both.
   - Guards in `update_plan` (`:1315`), `apply_suggestions` (`:1369`),
     `process` (`:1446`).
   - Approve: router resolves `approver_display` (`routes/plan_pipeline.py`
     approve `:727-746`, scoped proxy approve) and passes it in.
   - Emit: `PlanService.emit_contract()` (`:2412`, holds the lease) and
     `_emit_contract()` (`:2975-3070`, project fallback `:3094`, result
     writes `:3177-3194`); `routes/plan_pipeline.py:1030-1089` must not
     apply body overrides for artifact sessions.
     `plan/emit/contract_emit.py`: `emit_contract(..., allow_fallback,
     require_signed)`; fallback branch `:447` requires
     `allow_fallback and env opt-in`; signing gate `:392-393` also fires on
     `require_signed`; readback (`:249-292`) never re-POSTs for artifact
     sessions; timeout/disconnect/5xx after send → `outcome="unknown"`;
     `assemble_contract` (`:125-204`) adds the three provenance fields.
   - Sync `apps/backend/plan/emit/contracts/task-contract.schema.json` and
     `HUB_SHA` after step 1 merges.
   - Tests in `tests/test_contract_emit.py`, `tests/test_plan_emit_lease.py`,
     `tests/test_mcp_stdio_proxy.py`, new `tests/test_ingest_approved.py`:
     blob mismatch, missing stage, bad bounds, no steps → 422; injection
     payload scanned and flagged; `ac-child-coverage` passes on real
     triples; hard failure blocks; unknown and invisible project identical
     404 and no cross-org dedupe; intent/spec edit not deduped; concurrent
     submits → one session (shared store); emitted session repeat → stored
     result, zero POSTs; lost response after create → attempt `unknown`,
     retry returns the same AIFactory task (fake idempotent server); legacy
     and non-user live emit 409; `PFACTORY_EMIT_FALLBACK=1` no fallback;
     unsigned refused; edit/apply/reprocess 409; staged guards unchanged;
     dry-run no POST; contract carries signed provenance.
   → verify `apps/backend/.venv/bin/pytest tests/ apps/web-server/tests/ -m "not slow" -q`,
   ratchet, `apps/backend/.venv/bin/python scripts/check_schema_drift.py --canonical <Factory schema worktree>/apis/task-contract.schema.json`.
   Traps: HMAC covers all but `approval`; never put fields in `approval`;
   REST `emit-contract` defaults `require_user_approval=False`, so artifact
   rules live in the service; D6 wording.

4. **TFactory** (`feat/3865-no-register`).
   `apps/web-server/server/routes/specs.py`: `SpecIngestRequest` (`:70`)
   adds `no_register`; registration branch `_clone_and_register_project`
   (`:491-498`, 404 `:499-503`) honours it. `mcp_stdio/router.py` scoped
   ingest (`:333-349`, `:344`) returns the identical 404 when
   `no_register`. `apps/backend/agents/tools_pkg/tools/task_control.py`
   (`create_spec_ingest_workspace`, `planner_scheduled` `:995-1027`,
   `:1235-1269`) adds `planner_reason`. Vendored schema subset gains the
   three provenance fields (A5). Tests in root `tests/test_mcp_stdio_authz.py`
   (scoped ingest with `repo` + `no_register`: unknown vs invisible
   identical; narrow `acw_` key without `api:full` ingests with `pr_number`
   and reads `/api/mcp-stdio/spec-tasks/...`) and the task_control tests for
   each reason.
   → verify `apps/backend/.venv/bin/pytest tests/ -q`,
   `apps/backend/.venv/bin/pytest apps/web-server/tests/ -m "not slow" -q`,
   ratchet, `scripts/check_schema_drift.py`.
   Traps: #1597 bare-id contract; D6 wording from #1651.

5. **AIFactory** (`feat/3865-from-plan-idempotent`).
   `from-plan` (`apps/web-server/server/routes/execution.py`, handler
   ~`:1362-1669`; `trusted_plan.py` `ingest_trusted_plan`): look up an
   existing spec for `(project, provenance.submission_id)` before
   allocating; repeat → 200 with the same ids. Tests: root
   `tests/test_trusted_plan.py` (contract with the provenance fields
   verifies and is stored verbatim; tampering any field fails) and
   `apps/web-server/tests/test_from_plan_route.py` (repeat submission_id →
   same task, one spec dir; absent → unchanged). Use a PFactory-signed
   fixture contract produced by step 3's emitter in dry-run (checked in as
   JSON) for the cross-repo check.
   → verify the CI backend and web-server suites + ratchet.
   Traps: no `AIFACTORY_SKIP_PYTHON_GATE`; project scoping (P3) on lookup.

6. **Factory CLI package** (`feat/3865-factory-cli`).
   `shared/factory-cli/pyproject.toml` (Python ≥ 3.11, deps `pyyaml`,
   `mcp` pinned range, entry point `factory = factory_cli.__main__:main`),
   `factory_cli/{__main__,config,transport,gitartifacts,submit,testrun,status,doctor,mcp_server,mcp_config}.py`,
   `factory_cli/_vendor/factory_common/` (A9). Implements every CLI
   decision above. Tests `shared/factory-cli/tests/`: temp git repo for
   approval-commit cases (approved, edit after approval, missing approve
   commit, dirty tree, SHA-256 repo); blob framing; key order; canary key
   never printed (stdout, stderr, MCP JSON); URL policy (userinfo,
   non-loopback http, redirect); stdlib `http.server` fake portals for
   submit 0/1/3 and lost-response (one emit POST, exit 3), test, status;
   `mcp-config` output secret-free for all three agents; `--write` refusal,
   `--force` replacing a Codex table with sibling tables intact and the file
   still valid TOML, `.bak` 0600, mode kept; vendor drift test.
   → verify `cd shared/factory-cli && python -m pytest -q`, `ruff check
   shared/factory-cli`, `mypy --strict shared/factory-cli/factory_cli`.
   Traps: `mcp` imported only in `mcp_server.py`; redact error bodies;
   `tomllib` read-only.

7. **Factory packaging and CI** (same PR as 6).
   `flake.nix`: `packages.<system>.factory-cli` (buildPythonApplication,
   `src = ./shared/factory-cli`, nixpkgs `python3Packages.{pyyaml,mcp}`),
   `apps.<system>.factory`. `.github/workflows/contracts.yml` (or the
   code-quality workflow) collects `shared/factory-cli/tests/` and runs
   `ruff check shared/factory-cli`. `shared/factory-cli/README.md`: both
   install paths, `doctor`, `mcp-config`.
   → verify `nix build .#factory-cli && ./result/bin/factory --help`,
   `nix run .#factory -- --help`, `uv tool install --force ./shared/factory-cli && factory --help`
   (local only; live `doctor` is step 8).
   Traps: build on p620 only; never touch `~/.config/nixos`; if nixpkgs'
   `mcp` is outside the pinned range, widen the range or report.

8. **Releases and live acceptance** (lead).
   Merge in the order above; release TFactory, PFactory, AIFactory (minor)
   per RELEASE.md (TFactory: no manual tag). Then from clean Claude Code,
   Codex and agy sessions: `factory doctor`; `factory submit` of this
   issue's triple against `aifactory-demo`, `--dry-run` then live;
   re-run the live submit (same task id returned); AIFactory shows the task
   without a planning phase and the stored contract carries the provenance;
   `factory status pf:` shows PFactory + AIFactory; `factory test` schedules
   a TFactory run and `status tf:` follows it; a key without access gets the
   same "not found" as an unknown project. Record results on #3865.

## Tests

Per step above; CI green on all five PRs; step 8 checklist passes.

## Rollback

CLI: revert the Factory CLI PR; per-portal MCP entries untouched. PFactory:
new route, fields and parameters are additive; revert or redeploy the
previous image via gitops; existing artifact sessions stay readable.
AIFactory: idempotency applies only when `submission_id` is present;
revert to restore per-POST allocation. TFactory: `no_register` defaults
false; revert. Schema: optional fields, `additionalProperties: true`.
