---
status: approved
issue: 3865
intent: intent/2026-11-23-3865-factory-cli.md
---

# Spec: one `factory` CLI and MCP front door (release 1)

Release 1 only: CLI + MCP over each portal's existing scoped key. Fleet login
is a separate intent (intent, open question 2). File references are
`origin/dev` (products) or `origin/main` (Factory) on 2026-11-23; the plan
re-pins exact lines.

## Design

### D1. Package `shared/factory-cli` (Factory repo)

- Python ≥ 3.11 package `factory_cli` with `pyproject.toml` (the first
  installable package under `shared/`), entry point `factory`.
- Deps: stdlib `urllib`; `pyyaml`; `mcp` (the SDK the portal stdio servers
  use), imported only by `factory mcp`, pinned to a range.
- `factory_common` (`url_safety`, `secrets`) is vendored into the package at
  build time, like `factory-ui`.
- `flake.nix` adds `packages.<system>.factory-cli` and `apps.<system>.factory`
  (today only `devShells`). Non-NixOS hosts: `uv tool install ./shared/factory-cli`.
  Both install paths get a smoke test.

### D2. Configuration, credentials, transport

- Portal URL: `<P>_API_URL`, else `~/.config/factory/config.toml`
  `[portals.<p>] url`. The file holds URLs only.
- Key, in the stdio clients' order (`*/agents/tools_pkg/http_client.py`):
  `$<P>_MCP_KEY` → `~/.<p>/.mcp-key` → `$<P>_API_TOKEN_FILE` → `~/.<p>/.token`.
  CFactory: `$CFACTORY_API_KEY` → `~/.cfactory/.mcp-key`.
- The CLI calls the scoped proxies under `/api/mcp-stdio/*` (PFactory,
  AIFactory, TFactory), which accept narrow `acw_` keys. General `/api/*`
  requires `api:full` for `acw_` keys (TFactory `server/auth.py:269-304`).
  CFactory uses `/api/workitems/*` and `/health`.
- Transport policy (CLI's own, not `assert_safe_outbound_url`'s default):
  https, or http only to loopback. URLs with userinfo are rejected. Requests
  go through `factory_common`'s no-redirect opener. The loaded key value
  and recognised secret patterns are redacted from every error, warning and
  MCP result.
- `factory doctor`, per portal: URL, key source (name only), health version
  (`/api/health`; CFactory `/health`), and whether the key works, checked
  with a protected read (the project list).
- Live `factory submit` needs a **user** `acw_` key on PFactory. A legacy
  shared token can dry-run, but a live emit returns PFactory's 409 (the same
  rule as MCP `plan_emit`). `doctor` says which kind of key it found. Nothing
  existing loses access; the CLI only declines what PFactory already refuses.

### D3. `factory submit`

#### Client (in a git worktree)

1. Resolve `intent/<slug>.md`, `spec/<slug>.md` and `plan/<slug>.md`.
   - Each must be committed with frontmatter `status: approved`, and the
     working copy must equal HEAD. Otherwise exit 2, naming the file.
2. For each stage, find the approval commit. That is the newest commit on
   the current branch (an ancestor of HEAD) that:
   - changes that file's frontmatter from `draft` to `approved`; and
   - after which the file's blob is unchanged up to HEAD
     (`git rev-parse <commit>:<path>` == `HEAD:<path>`).
   - An edit made after approval fails with exit 2: "re-approve <stage>".
3. SHA-1 repositories only. A SHA-256 repo exits 2 ("not supported in
   release 1").
4. Repo identity: the normalized `origin` (`provider`, `owner/repo`), the
   current branch, the HEAD SHA, and `--base` (default: the remote's
   default branch).
5. Sequence: `ingest-approved` → `approve` → `emit-contract` (live), all
   under `/api/mcp-stdio/plan/...`.
   - With `--dry-run`, the CLI stops after a dry-run emit and prints the
     children and contract summary, with no downstream POST.
6. Output and exit codes. Every outcome prints the PFactory session id and
   the correlation key.
   - Success: also the AIFactory task id. Exit 0 only when
     `contract_result.ok`.
   - Portal 4xx: the safe `detail`. Exit 1.
   - Outcome unknown: a timeout, a 5xx after the emit was sent, or a
     PFactory 200 with `outcome="unknown"`. The message says "outcome
     unknown". Exit 3.
   - The CLI never repeats an emit POST. `factory status pf:<session>`
     reconciles an unknown outcome.

#### PFactory (new, additive)

- **Mapper, `plan/ingest/artifacts.py`.** Modelled on
  `plan/ingest/speckit.py` (`ingest_speckit`). It maps the triple to
  `(NormalizedPlan, EpicPlan)`:
  - `NormalizedPlan`:
    - title from the intent H1;
    - description from the intent's Problem and Proposed outcome;
    - one criterion per bullet in the spec's `## Verification`;
    - `content_trust="untrusted_user_content"`, so the intake injection scan
      runs (`service.py` `_scan_intake_text` skips any other value);
    - `source_format="factory-artifacts"`.
  - `EpicPlan`:
    - one `ChildIssue` per numbered item under the plan's `## Steps`, in
      order, each `depends_on` the previous one;
    - `body` is the step text (with its verify and Traps lines);
    - `kind` is inferred by the speckit task mapping, defaulting to
      `"implementation"`, and a step that names tests or CI is
      `testing`/`ci`.
    - Every spec criterion is attached verbatim to the
      `acceptance_criteria` of the final child. This satisfies the hard
      `ac-child-coverage` check (`readiness/checks.py:304-342`) without
      adding or rewriting a step.
  - Plan-wide sections reach the executor:
    - the plan's `## Tests` and `## Rollback` are appended to the final
      child's body;
    - the whole plan text goes into `epic_body`.
  - A plan with no parseable `## Steps` is rejected with 422. It is never
    decomposed.
- **Route, `POST /api/mcp-stdio/plan/sessions/ingest-approved`.** Scope
  `plan:write`, the same `_PlanWrite` dependency as `ingest-text`.
  - Body (`extra=forbid`): `slug`, `provider`, `repo`, `base_ref`,
    `branch`, `head_sha`, `project_id?`, and exactly one entry per stage in
    `artifacts: [{stage, path, content, blob_sha, approve_commit_sha,
    approve_commit_author}]`.
  - Field limits: SHAs are 40 lowercase hex. The author is printable, at
    most 200 characters, with no control characters. The path is
    repo-relative, at most 512 characters, with no `..`.
  - The server recomputes each blob SHA with git framing
    (`sha1(b"blob %d\0" % len(raw) + raw)` over the exact UTF-8 bytes) and
    rejects any mismatch with 422.
  - Authorization runs first: the same tenant/org and repo-project role
    checks as `ingest-text` (`routes/plan_pipeline.py` `_ingest_org`,
    `require_repo_project_role`). An unknown or invisible project gets the
    identical 404.
  - It then runs the `process()` stages that do not author content:
    classification (detect / plan-type apply, so `target_kind`,
    `plan_type` and the tier are set), grounding, the intake injection scan
    (a flagged plan is routed to the hard tier as usual), the review runner
    and `run_gates`, `annotate_plan`, and `_save`.
  - It skips `_decompose` and `synthesize`, and sets `session.epic` from
    the mapper.
  - The resulting `PlanReview` is stored as `process()` stores it.
    Hard-gate failures block approval and emit as they do today. Soft lens
    findings are shown. Nothing is auto-waived.
  - `session.source_artifacts` is a new optional field. A session that
    carries it refuses the edit, suggestions/apply and reprocess routes
    with 409 "artifact session: re-submit instead".
- **Dedupe.** The key is `(tenant, org, project, provider, repo, base_ref,
  intent/spec/plan blob SHAs)`.
  - It is computed after authorization.
  - The key is claimed atomically through the store's existing
    claim/lease mechanism (`acquire_emit_lease`), the same one `from-issue`
    uses to stay single-session across replicas.
  - A repeat returns the existing session id (200).
  - When that session is already emitted, `approve` and `emit-contract`
    return the stored `contract_result` unchanged and never POST again.
    Re-approval of an emitted artifact session is refused.
- **Emit for artifact sessions.** The service enforces all of these
  whichever route calls it:
  - live emit always has `require_user_approval=True`;
  - a signing key is required (no unsigned contract);
  - `allow_fallback=False`, a new explicit parameter on `emit_contract`.
    `PFACTORY_EMIT_FALLBACK` cannot apply, and the `handoff` block is not
    used, because it means PR identity to AIFactory;
  - the repo and project come from the session, and body overrides are
    ignored. A missing project is a 422, never the plan id used as the
    project;
  - the emit lease from the existing path is held, so the downstream POST
    happens at most once per session.
- **Contract.** `assemble_contract` adds two fields when
  `session.source_artifacts` is set:
  - `provenance.source_artifacts`: a list of `{stage, path, blob_sha,
    approve_commit_sha, approve_commit_author, attested_by: "client"}`. The
    commit fields are labelled client-attested, so PFactory signs that it
    received them, not that it verified git.
  - `provenance.human_approval`: `{user_id, approver_display, auth_kind,
    approved_at, plan_hash}`. `user_id` is the authenticated key owner from
    `review.human_approval.user_id`. `approver_display` is derived
    server-side from that user, never from the client's `approver` string.
  - Both fields are inside the HMAC and Ed25519 coverage, because
    `_plan_core` excludes only `approval` (`signing.py:49-81`; AIFactory
    `trusted_plan.py:195-226` verifies the same way).
    `approval.approved_by` stays `"pfactory"`.

#### Factory and AIFactory

- `apis/task-contract.schema.json` declares both provenance fields as
  optional typed properties, still with `additionalProperties: true`.
  Regenerate `shared/factory-contracts` and sync PFactory's vendored copy.
- AIFactory needs no code change. `from-plan` verifies signature,
  completeness and handoff identity, then stores the contract verbatim
  (`trusted_plan.py` `ingest_trusted_plan`). Add tests that:
  - a contract with both fields verifies and is stored;
  - tampering with any field breaks the signature.

### D4. `factory test`

`factory test --project ID (--spec SLUG | --criteria FILE) --branch B [--pr N]`

- **Project.** `--project` is required in release 1. Matching projects by
  repo is dropped, because TFactory's project list has no repo origin
  (`project_to_response`).
  - TFactory adds `no_register: true` to the scoped ingest proxy
    (`/api/mcp-stdio/specs/ingest`). With it, an unknown project id is
    never auto-registered.
  - An unknown project and an invisible one both return the identical 404.
    The CLI prints "project not found" and exits 1 for both, and the MCP
    result is the same for both.
- **Criteria.**
  - `--spec` takes the bullets under `spec/<slug>.md` `## Verification`.
  - `--criteria` takes a file's bullets.
  - Either way, the CLI rewrites them as an `## Acceptance Criteria`
    section with `AC1..ACn`, the format TFactory's Markdown parser expects
    (`apps/backend/spec_sources.py`). No criteria means exit 2.
- **Run id.** `<slug|criteria-stem>-<head short sha>-<utc yyyymmddHHMMSS>`,
  limited to `[a-z0-9-]` and 64 characters, so branches with `/` work and
  separate runs never collide.
- **Ingest call.** It sends `project_id`, `spec_id`, `spec_text`,
  `source_branch`, the repo, `pr_number` (verdict reporting) and
  `no_register: true`.
  - The CLI records the branch head SHA it saw and prints it next to the
    SHA TFactory checked out. A mismatch is a warning.
- **Result.** Prints `workspace_project_id` and `spec_id`.
  - `planner_scheduled=false` is reported with its reason: the checkout
    failed, the scheduler failed, or `TFACTORY_AUTO_PLAN` is off.

### D5. `factory status`

`factory status <pf:SESSION | af:TASK | tf:PROJECT/SPEC | correlation key>`

- `pf:` reads the PFactory session through the scoped proxy: status,
  `contract_result` and the correlation key. From `contract_result` it
  follows the AIFactory task id.
- `af:` reads `GET /api/mcp-stdio/tasks/{task_id}` (AIFactory).
- `tf:` reads `GET /api/mcp-stdio/spec-tasks/{project}/{spec}` (TFactory),
  using the workspace project id.
- A bare correlation key reads CFactory `GET /api/workitems/{key}`, and
  only when a CFactory key is configured. That key's view is tenant-wide;
  the help text and `doctor` say so.
  - Without a CFactory key, or on 401/403, the CLI exits 2: "use pf:/af:/tf:
    ids".
- `submit` and `test` print the ids to use.
- Every 404, CFactory's included, prints "not found" with no portal detail.

### D6. `factory mcp` and `factory mcp-config`

- **`factory mcp`** is a stdio MCP server with the tools `factory_submit`,
  `factory_test`, `factory_status` and `factory_doctor`.
  - They are the same functions as the CLI, return JSON, and carry the same
    outcome field (`ok` / `rejected` / `unknown`).
  - `factory_submit` defaults to `dry_run=true`.
- **`factory mcp-config claude|codex|agy`** prints an entry with command
  `factory mcp` and no keys or secret env, in each agent's format:
  - Claude: `.mcp.json`;
  - Codex: a `~/.codex/config.toml` `[mcp_servers.factory]` block;
  - agy: `~/.gemini/config/mcp_config.json`.
- **`--write`** merges the entry into the agent's config file:
  - It adds only the `factory` entry, and refuses if one exists unless
    `--force` is given.
  - JSON files are parsed and rewritten. The TOML file is append-only: the
    CLI checks with `tomllib` that no `[mcp_servers.factory]` table exists,
    then appends the block. It needs no TOML writer.
  - It writes a `.bak` with mode 0600, then replaces the file atomically
    (temp file + rename), keeping the original mode.

## Alternatives rejected

- **Calling the existing four routes, including `plan_process`.** It
  re-decomposes the approved plan.
- **Passing a pre-built epic through `ingest-text`.** That route is
  `extra=forbid`, and every caller would gain a way to skip decomposition.
- **Trusting git commits as authorization.** PFactory cannot verify a
  client's git history. The user-key approval authorizes; the commits are
  signed as client-attested evidence.
- **Reusing the `handoff` block to switch off fallback.** It means PR
  identity to AIFactory (`validate_handoff_identity`) and would need a PR
  number and a distinct source branch.
- **Matching TFactory projects by repo.** The data isn't exposed, and
  auto-registering would create an existence oracle. Deferred to the
  registry cutover (#3857 phase 3).
- **A TOML library to merge Codex config.** Append-only covers the one
  table we add.
- **A Go binary.** It shares no code with the Python portals and contracts.

## Risks

- **The new `## Steps` parser.** Unparseable plans are rejected. Tests run
  against this repo's real plans. `--dry-run` shows the children first.
- **Gates on authored plans.** The best-practices lens may score low
  without CI or testing children. Its findings are soft and shown, but a
  hard failure blocks the submit until the plan is fixed. That is intended.
- **Client-attested commit fields.** They are evidence, not authorization.
  They are labelled as such in the contract, and their format is bounded.
- **Legacy-token users** cannot live-submit. This is the existing PFactory
  rule; `doctor` explains it.
- **CFactory's tenant-wide status view** is documented, and it is used only
  with a configured CFactory key.
- **`mcp` SDK drift** is limited by the pinned range.

## Verification

- **Factory unit:**
  - approval-commit detection on a temp git repo: the approved case, an
    edit after approval, a missing approve commit, a dirty working copy,
    and a SHA-256 repo;
  - git blob framing;
  - key lookup order;
  - redaction of the loaded key value;
  - URL policy: userinfo, non-loopback http, redirects;
  - exit codes 0/1/2/3;
  - `mcp-config` output has no secrets for all three agents;
  - `--write`: refusal without `--force`, `.bak` mode 0600, atomic replace,
    TOML append.
- **PFactory unit:**
  - the mapper on this repo's real triples, checking step order and text,
    criteria on the final child, and Tests/Rollback folded in;
  - the route rejects a blob mismatch, a missing stage, unparseable steps
    and oversized or invalid fields;
  - an injection payload is scanned and flagged;
  - `ac-child-coverage` passes;
  - a hard failure blocks approve and emit;
  - unknown and invisible projects both get 404, and dedupe never returns a
    session across orgs;
  - an intent or spec edit does not dedupe;
  - concurrent submits produce one session;
  - re-submitting an emitted session returns the stored result without a
    POST;
  - a lost response after the downstream create does not POST again;
  - legacy-token and non-user live emits get 409;
  - `PFACTORY_EMIT_FALLBACK=1` does not fall back;
  - the edit and reprocess routes return 409 on artifact sessions;
  - staged-session guards are unchanged;
  - a dry run makes no downstream POST.
- **Offline end-to-end:** a real approved triple goes through PFactory to
  AIFactory's `from-plan` with test keys. The signature verifies, and the
  stored contract carries both provenance fields. Tampering with each field
  is rejected.
- **AIFactory unit:** the contract with the new fields verifies and is
  stored.
- **TFactory unit:**
  - with a narrow `acw_` key: ingest, the PR number and status through
    `/api/mcp-stdio/*`;
  - `no_register` with an unknown project gives the same 404 as an
    invisible one;
  - the `planner_scheduled=false` reasons.
- **Schema:** the contract validates with and without the new fields, and
  the regenerated package matches.
- **Live (after the PFactory and TFactory releases):**
  - from clean Claude Code, Codex and agy sessions, `factory submit`
    this issue's own artifacts against `aifactory-demo`, first `--dry-run`
    and then live;
  - AIFactory shows a task with no planning phase, and the stored contract
    carries the provenance;
  - `factory status pf:` shows both PFactory and AIFactory;
  - `factory test` schedules a TFactory run and `status tf:` follows it;
  - a key without access to the project gets the same "not found" as an
    unknown project;
  - both install paths pass the smoke test.

## Amendments (plan review, 2026-11-23)

Found by the Codex and agy plan reviews; these supersede the text above where
they differ.

- **A1 Child kinds.** PFactory's `ChildKind` (`plan/decompose/models.py:16`)
  has `task`, `testing`, `cicd`; the mapper uses `task` by default and
  `testing` / `cicd` for steps naming tests or CI (not `implementation` /
  `ci`).
- **A2 Idempotent downstream create.** A lease does not record that a POST
  was sent, so a lost response could still create a second AIFactory task.
  The signed contract carries `provenance.submission_id` (hex SHA-256 of the
  dedupe key). AIFactory `from-plan` becomes idempotent on
  `(project, submission_id)`: a repeat returns the existing task (200, same
  id) instead of allocating a new spec. PFactory persists an attempt record
  (`sent` → `ok|rejected|unknown`) on the session before and after the POST;
  with A2 in place, a retry after `unknown` is safe and returns the original
  task. Timeout, disconnect and 5xx after send all map to `unknown`.
- **A3 Registered project only.** `ingest-approved` resolves the project from
  the registry (not `_ingest_org(for_intake=True)`, which can authorize an
  unregistered repo); unknown and invisible both return the identical 404.
  The dedupe claim follows `from-issue`'s pattern (insert claim row, lease,
  re-check, owner-only cleanup, `service.py:1097-1161`); a concurrent caller
  waits for the holder and then gets 200 with the existing session; a
  claim whose holder died expires with the lease TTL.
- **A4 Signing.** `emit_contract` gains `require_signed`; artifact sessions
  set it, so a missing key fails rather than emitting unsigned.
- **A5 Schema rollout.** `scripts/gen_contracts.py` does not read the task
  contract schema; no regeneration is needed. Order: TFactory's vendored
  schema subset gains the fields first (its drift check follows Factory
  `main`), then the Factory schema PR, then PFactory's copy + `HUB_SHA`.
- **A6 `mcp-config --write --force` for Codex** replaces the existing
  `[mcp_servers.factory]` table and its `[mcp_servers.factory.*]` subtables
  textually, then validates the whole file with `tomllib` before the atomic
  write; without `--force` an existing table is refused.
- **A7 Defaults.** Default portal URLs are
  `https://{pfactory,aifactory,tfactory}.freundcloud.org.uk` and
  `https://cfactory-mcp.freundcloud.org.uk` (the main CFactory host
  redirects to SSO). CFactory key lookup adds `~/.cfactory/.token` last, as
  the other portals do. The owner's existing `~/.<p>/.token` keys are user
  `acw_` keys (checked live 2026-11-23) and work on the scoped routes.
- **A8 PFactory scoped `ingest-text`** returns 422, not 500, on an invalid
  body (seen live 2026-11-23; same proxy pattern as the new route).
- **A9 Packaging.** `factory_common` modules the CLI needs are committed
  under `shared/factory-cli/factory_cli/_vendor/` with a drift test against
  `shared/factory-common`, so both `uv tool install ./shared/factory-cli`
  and the Nix build work from the package directory alone.
- **A10 Run ids** for `factory test` add 6 random hex characters, so rapid
  repeats never collide.
