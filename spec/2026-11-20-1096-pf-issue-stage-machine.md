---
status: approved
issue: 1096
intent: intent/2026-11-20-1096-pf-issue-stage-machine.md
---

# Spec: issue → intent/spec/plan stage machine on a draft PR with bound /approve

## Design

1. **Boundary with companion `olafkfreund/AIFactory#2172`.**
   - **PFactory (`#1096`)** owns: durable stage job intake from `POST /api/plan/sessions/from-issue`, reading `.factory.yml` and `CODEOWNERS` from the trusted base branch, cutting branch `factory/<n>-<slug>`, committing `intent/<slug>.md`, `spec/<slug>.md`, and `plan/<slug>.md` across stages, opening the draft PR, polling and verifying bound single-use `/approve <stage>` comments on the draft PR, updating issue labels `factory:intent` → `factory:spec` → `factory:plan` → `factory:building` (or `factory:needs-human`), and emitting the signed Task Contract v2 with the handoff identity to AIFactory's configured `/api/tasks/from-plan`.
   - **AIFactory (`#2172`)** owns: passing `base_branch` and `project_id` in `intake_poller._route_hard` (`/mnt/code/Source-home/GitHub/AIFactory/apps/web-server/server/services/intake_poller.py:327-362`), adopting the existing `source_branch`, `approved_head_sha`, and `pr_number` in `/api/tasks/from-plan` (`/mnt/code/Source-home/GitHub/AIFactory/apps/web-server/server/routes/execution.py:1362-1573`), coding on that branch, sending `fix-completed` to the configured TFactory origin, and marking the draft PR ready for review (never auto-merging) after TFactory verifies the exact head SHA.

2. **Durable stage job & idempotent `from-issue` intake.**
   - Extend `FromIssueBody` (`apps/web-server/server/routes/plan_pipeline.py:80-100`) with optional `base_ref: str | None = None`, `base_branch: str | None = None` (alias sent by AIFactory's poller), and `project_id: str | None = None`. Effective `base_ref` is `body.base_ref or body.base_branch`.
   - Extend `PlanSession` (`apps/backend/plan/service.py:99-195`) with additive fields: `provider: str = "github"`, `project_id: str | None = None`, `stage_state: StageJobState | None = None`.
   - `StageJobState` (defined in new `apps/backend/plan/stage_machine.py`) holds:
     - identity: `tenant_id`, `provider`, `repo`, `issue_number`, `project_id`, `base_ref`, `base_sha`, `source_branch` (`factory/<issue_number>-<short_slug>`), `artifact_slug` (`YYYY-MM-DD-<issue_number>-<short_slug>`), `pr_number`, `pr_url`;
     - stage progression: `current_stage` (`"intent" | "spec" | "plan" | "approved" | "building" | "needs_human"`), `stage_status` (`"draft" | "approved" | "failed"`), `head_sha` (exact commit SHA of the current draft on `source_branch`), `last_comment_since` (ISO timestamp cursor for `fetch_pr_comments`);
     - single-use approval ledger: `approvals: list[BoundApproval]` (`stage`, `pr_number`, `head_sha`, `comment_id`, `provider_user_id`, `username`, `policy_source`, `approved_at`) and `consumed_comment_ids: list[str]`.
   - `PlanService.ingest_from_issue` deduplicates by `(tenant_id, provider, repo, origin_issue_number)` across active sessions in `PlanSessionStore` (`apps/web-server/server/jobstore/plan_session_store.py:41-399`): if a non-terminal session already exists for that tuple, return it without minting a second session or PR.
   - To eliminate the hard-tier stall on dev, `ingest_from_issue` schedules the stage job bootstrap off the event loop via `server.background.tasks.spawn` (`apps/web-server/server/background/tasks.py:55-60`) under the session's `_emit_lock` (`apps/backend/plan/service.py:2175-2228`).

3. **Base-branch `.factory.yml` and `CODEOWNERS` policy reader.**
   - Using the read-only reconnaissance clone seam (`clone_for_recon(repo, base_ref)` in `apps/backend/plan/recon/clone.py:186-239`, which resolves default branch when `base_ref` is `None` and disables hooks/submodules/askpass-argv leaks), read from the **trusted base branch** only:
     - `.factory.yml`: parse minimum repo policy (`tiers.<tier>.stages` defaulting to `["intent", "spec", "plan"]` for `hard`, `approvers`, `verify.lanes`, `base_branch` fallback).
     - `CODEOWNERS` (checked at `CODEOWNERS`, `.github/CODEOWNERS`, `docs/CODEOWNERS`, `.gitlab/CODEOWNERS`): last-matching gitignore-style pattern wins for the stage file path (`intent/<artifact_slug>.md`, `spec/<artifact_slug>.md`, `plan/<artifact_slug>.md`).
   - Policy evaluation is explicit, never either-or:
     1. If `.factory.yml` `approvers` or `CODEOWNERS` on the base branch matches the stage file path, the commenter's resolved identity must match an declared owner (`@username` matched only after verifying the commenter's immutable provider user id matches that login's immutable provider user id).
     2. Only when no `CODEOWNERS` rule (and no `.factory.yml` stage approver list) matches the stage file path does policy fall back to repository `write` or `admin` permission on the base repository.

4. **Draft PR creation & stage commits.**
   - On bootstrap (`current_stage = "intent"`):
     - Run `PlanService.process_async(session_id)` so reconnaissance, injection scan (`Factory#273`), and initial decomposition populate the session.
     - Render `intent/<artifact_slug>.md` with `status: draft`, `issue: <n>`, `author: factory` using the repo's artifact-workflow template (`Problem`, `Proposed outcome`, `Affected users and systems`, `Constraints`, `Open questions`).
     - In a hardened temporary checkout of `base_ref` (`clone.py:129-162` `_hardened_env` + `core.hooksPath=/dev/null`), create branch `factory/<n>-<short_slug>`, write `intent/<artifact_slug>.md`, commit, push to `origin`, and record `head_sha = git rev-parse HEAD`.
     - Open a **draft** PR (`gh pr create --draft --repo <repo> --base <base_ref> --head <source_branch>` on GitHub; `provider.create_pr(..., draft=True)` on GitLab, `apps/backend/runners/github/providers/gitlab_provider.py:263-306`), swap issue labels to `factory:intent` (removing `factory:queued`), and post a status comment on the issue with the draft PR link and `/approve intent` instructions.

5. **Bound, single-use `/approve <stage>` verification & stage transitions.**
   - Web-server lifespan (`apps/web-server/server/main.py:157-221`) starts `stage_poller_loop` (gated by `APP_STAGE_POLLER_ENABLED`, default `True` when intake/stage sessions exist, plus an explicit `POST /api/plan/sessions/{session_id}/poll-stage` trigger for deterministic tests and webhook-free runs).
   - On each tick for an active stage session (`current_stage in {"intent", "spec", "plan"}` and `stage_status == "draft"`):
     1. Fetch current PR metadata and current remote `head_sha` (`GHClient.get_pr_head_sha` in `apps/backend/runners/github/gh_client.py:798-825`). If the PR was closed/merged externally, mark the session `discarded` / `needs_human`.
     2. Read PR comments via `provider.fetch_pr_comments(pr_number, since=last_comment_since)` (`apps/backend/runners/github/providers/protocol.py:614-639`, `github_provider.py:395-400`).
     3. For each unconsumed comment (`comment.id not in consumed_comment_ids`) created strictly at or after the current stage commit timestamp:
        - Match command regex `^\s*/approve\s+(intent|spec|plan)(?:\s|$)` (exact stage token).
        - Require `matched_stage == stage_state.current_stage`.
        - Require the PR's live `head_sha == stage_state.head_sha` (if a human or agent pushed a new commit to `source_branch`, `stage_state.head_sha` updates to the new commit only after any prior approval for that stage is invalidated, and comments posted before the new commit's timestamp are rejected as stale-SHA).
        - Resolve immutable user id from `comment.raw_data`: on GitHub `raw_data["user"]["id"]` (int > 0, cross-checked against `/users/{login}` id via `gh api` so a renamed account cannot spoof a `CODEOWNERS` handle); on GitLab `raw_data["author"]["id"]` (int > 0, verified via exact member endpoint `GET /api/v4/projects/{id}/members/all/{user_id}`).
        - Verify base-branch policy (CODEOWNERS for `<stage>/<artifact_slug>.md`, else `write`/`admin`). On refusal, record `comment.id` in `consumed_comment_ids`, post a brief refusal comment naming the policy rule, and do not advance.
     4. On valid approval:
        - Append `BoundApproval` and `comment.id` to `consumed_comment_ids` (compare-and-set via `PlanSessionStore.upsert`, `plan_session_store.py:111-168`).
        - **If `intent`:** flip `intent/<artifact_slug>.md` frontmatter to `status: approved`, render and write `spec/<artifact_slug>.md` (`status: draft`, `intent: intent/<artifact_slug>.md`), commit both, push to `source_branch`, update `stage_state.head_sha` and `current_stage = "spec"`, label issue `factory:spec`.
        - **If `spec`:** flip `spec/<artifact_slug>.md` to `status: approved`, re-run/refresh `PlanService.process` with the approved spec text, render self-contained `plan/<artifact_slug>.md` (`status: draft`, `spec: spec/<artifact_slug>.md`), commit both, push, update `stage_state.head_sha` and `current_stage = "plan"`, label issue `factory:plan`.
        - **If `plan`:** flip `plan/<artifact_slug>.md` to `status: approved`, commit and push to `source_branch`, record `approved_head_sha = new_head_sha`, call `PlanService.approve(...)` (`service.py:1953-1963`) recording the approval provenance from PFactory#1095 (W0) as `auth_kind="provider_review"` with `provider`, immutable `provider_user_id`, `username`, `pr_number` and `approved_head_sha`, and immediately run `PlanService.emit_contract(..., dry_run=False)`.

6. **Handoff identity in Task Contract v2 & GitLab exact-id fix.**
   - `build_task_contract` (`apps/backend/plan/emit/contract_builder.py:197-266`) and `assemble_contract` (`apps/backend/plan/emit/contract_emit.py:126-205`) populate `contract["provenance"]` and top-level `contract["handoff"]` with:
     `project_id`, `provider`, `repo`, `base_ref`, `source_branch`, `approved_head_sha`, `pr_number`.
   - The identity travels only inside the signed `provenance` (snake_case keys above); the `/api/tasks/from-plan` request body is unchanged.
   - On successful handoff, update issue label to `factory:building` and comment the AIFactory task id; on gate or handoff failure, apply `factory:needs-human` and post the actionable reason. Never enable or call auto-merge.
   - In `gitlab_provider.py` (`/mnt/code/Source-home/GitHub/Factory/shared/factory-github/providers/gitlab_provider.py:755-774` and vendored `apps/backend/runners/github/providers/gitlab_provider.py:781-799`), replace `params={"query": username}` + `resp.json()[0]` with exact user-id lookup (`GET /api/v4/projects/{id}/members/all/{user_id}` when `user_id` is passed or resolved via exact `/api/v4/users?username={username}` match where `u["username"].lower() == username.lower()`).

## Alternatives rejected

- **Reading `CODEOWNERS` or `.factory.yml` from the PR head branch:** allows an attacker or hallucinating agent on the PR branch to add themselves to `CODEOWNERS` and self-approve.
- **Treating `CODEOWNERS` and repo `write` access as either-or:** violates the epic security requirement; when `CODEOWNERS` covers the stage path, write access alone must not bypass code owners.
- **Approving via PFactory portal UI only:** forces maintainers out of GitHub/GitLab review and leaves the draft PR disconnected from the stage gates.

## Risks

- **Drift gate on `apps/backend/runners/github/`:** `.github/workflows/factory-github-drift.yml` enforces byte parity with `olafkfreund/Factory` at `HUB_SHA`. Editing `apps/backend/runners/github/providers/gitlab_provider.py` without updating the hub and `HUB_SHA` will fail the CI drift workflow. PFactory's `stage_machine.py` also enforces exact immutable user-id matching directly so security does not depend on the partial search even before a hub bump.
- **Concurrent poller replicas:** Two PFactory replicas polling the same PR could race on `/approve`. Mitigated by wrapping stage transitions in `PlanService._emit_lock(session_id, live=True)` (`service.py:2175`, Postgres `emit_lease_until`) and CAS `PlanSessionStore.upsert` (`plan_session_store.py:111`).

## Verification

- Unit & integration tests in `tests/test_issue_stage_machine.py`:
  - `from-issue` deduplicates by `(tenant, provider, repo, issue_number)`, reads `.factory.yml` from `base_ref`, creates `factory/<n>-<slug>`, commits `intent/<slug>.md`, opens a draft PR, and applies `factory:intent`.
  - `/approve intent` by a non-CODEOWNER (even with write access when CODEOWNERS covers `intent/`) is refused and consumed; by a base-branch CODEOWNER (verified by immutable provider user id) commits `intent` (`status: approved`) + `spec/<slug>.md` (`status: draft`) and applies `factory:spec`.
  - Replaying a consumed comment ID or approving after a new push (`head_sha` mismatch) is rejected and requires a fresh `/approve`.
  - When no `CODEOWNERS` rule covers the stage file, a user with base-repo `write`/`admin` succeeds and `read`/`none` is refused.
  - `/approve plan` commits `plan/<slug>.md` (`status: approved`), signs and emits the Task Contract carrying `project_id`, `provider`, `repo`, `base_ref`, `source_branch`, `approved_head_sha`, and `pr_number` to the configured AIFactory origin, and never auto-merges.
  - GitLab permission check rejects partial prefix matches (`"alice"` vs `"alice_admin"`) and queries `/members/all/{user_id}` by exact integer user ID.
