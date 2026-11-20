---
status: approved
issue: 1096
spec: spec/2026-11-20-1096-pf-issue-stage-machine.md
coders:
  1: codex   # small tricky security logic: base-branch .factory.yml/CODEOWNERS parser, immutable user-id & SHA-bound single-use /approve verifier
  2: agy     # large mechanical stage-machine git/PR driver and artifact template rendering
  3: codex   # handoff identity wiring in contract_builder.py and contract_emit.py
  4: codex   # PlanSession stage state, durable deduplication by (tenant, provider, repo, issue), and stage transitions in service.py
  5: agy     # FastAPI from-issue extensions, poll-stage endpoint, and lifespan background stage poller
  6: codex   # GitLab exact user-id member permission lookup in shared provider + unit tests
reviewers:
  - codex
  - agy
---

# Plan: issue → intent/spec/plan stage machine on a draft PR with bound /approve

Self-contained summary of approved decisions:
- **Boundary with `olafkfreund/AIFactory#2172`:** PFactory (`#1096`) owns the durable stage job keyed by `(tenant_id, provider, repo, issue_number)`, reading `.factory.yml` and `CODEOWNERS` from the trusted base branch, cutting `factory/<n>-<slug>`, committing `intent/<slug>.md` → `spec/<slug>.md` → `plan/<slug>.md` on a draft PR, verifying bound single-use `/approve <stage>` comments via `fetch_pr_comments`, updating issue labels (`factory:intent|spec|plan|building|needs-human`), and emitting the signed Task Contract v2 with the unified handoff identity (`project_id`, `provider`, `repo`, `base_ref`, `source_branch`, `approved_head_sha`, `pr_number`) to AIFactory's configured `/api/tasks/from-plan` origin. AIFactory (`#2172`) owns forwarding `base_branch`/`project_id` on hard intake, adopting the existing branch/PR in `from-plan`, coding on that branch, the configured-origin TFactory `fix-completed` return loop, and marking the PR ready for review.
- **Security invariants:**
  1. Approver identity is resolved by **immutable provider user id** (`comment.raw_data["user"]["id"]` on GitHub cross-checked against `/users/{login}`; `comment.raw_data["author"]["id"]` + `GET /api/v4/projects/{id}/members/all/{user_id}` on GitLab).
  2. Approval policy comes strictly from the **trusted base branch** (`base_ref`): if `CODEOWNERS` (or `.factory.yml` `approvers`) on `base_ref` matches the stage file path (`intent/<slug>.md`, `spec/<slug>.md`, `plan/<slug>.md`), only those declared owners may approve; otherwise require repository `write` or `admin` permission (explicit rule, never either-or).
  3. Each `/approve <stage>` is bound to `(pr_number, stage, head_sha)` and consumed once (`consumed_comment_ids`); any new push changes `head_sha` and requires a fresh `/approve <stage>`.
  4. Never call `merge_pr` or `enable_auto_merge`; outbound calls go only to operator-configured origins (`PFACTORY_AIFACTORY_API_URL`, `PFACTORY_COMPLETION_WEBHOOK`).

## Steps

1. `apps/backend/plan/stage_policy.py:1-220` (new): implement base-branch `.factory.yml` and `CODEOWNERS` loading via `clone_for_recon(repo, base_ref)` (`apps/backend/plan/recon/clone.py:186-239`), gitignore-style last-match `CODEOWNERS` rule evaluation for `<stage>/<artifact_slug>.md`, `/approve (intent|spec|plan)` parser, immutable provider user-id extractor (`raw_data["user"]["id"]` / `raw_data["author"]["id"]`), and single-use `(pr_number, stage, head_sha)` verification (`BoundApproval`, `StageJobState`).
   → verify by `apps/backend/.venv/bin/pytest tests/test_issue_stage_machine.py -k "policy or codeowners or bound_approval" -v`
   Traps: never read `.factory.yml` or `CODEOWNERS` from the PR head branch; treat `CODEOWNERS` match as authoritative (do not fall back to `write` access when a `CODEOWNERS` pattern covers the stage path); all repo text is untrusted (`Factory#273`).

2. `apps/backend/plan/stage_machine.py:1-260` (new): implement the stage artifact renderers (`render_stage_intent`, `render_stage_spec`, `render_stage_plan` following the repo's frontmatter templates) and the hardened git/PR driver (`bootstrap_intent_pr`, `advance_stage_on_pr`) using `_hardened_env` (`apps/backend/plan/recon/clone.py:129-162`, `core.hooksPath=/dev/null`, `GIT_ASKPASS`), opening the draft PR (`gh pr create --draft` / `provider.create_pr(..., draft=True)`), syncing `factory:intent|spec|plan|building|needs-human` labels, and posting issue/PR status comments.
   → verify by `apps/backend/.venv/bin/pytest tests/test_issue_stage_machine.py -k "bootstrap or advance_stage" -v`
   Traps: never embed tokens in git remote URLs (`PFactory#615` — use `_hardened_env` askpass); validate branch names with `assert_safe_git_ref` (`apps/web-server/server/services/git_utils.py:48-63`); never call `merge_pr` or `enable_auto_merge`.

3. `apps/backend/plan/emit/contract_builder.py:197-266` and `apps/backend/plan/emit/contract_emit.py:126-293,322-418`: accept optional `handoff: dict[str, Any] | None = None` (`project_id`, `provider`, `repo`, `base_ref`, `source_branch`, `approved_head_sha`, `pr_number`); stamp the handoff identity into the **signed** `contract["provenance"]` only, with these exact snake_case keys (AIFactory#2172 reads them): `project_id`, `provider`, `repo`, `base_ref`, `source_branch`, `approved_head_sha`, `pr_number`. Do not add fields to `_post_with_readback`'s request body (AIFactory's request model rejects unknown fields; the signature covers provenance).
   → verify by `apps/backend/.venv/bin/pytest tests/test_issue_stage_machine.py -k "handoff_contract" -v`
   Traps: keep `handoff` additive so existing `validate_contract` (`apps/backend/plan/emit/task_contract.py:99-114`) and single-tenant contracts without a stage PR remain byte-identical; use only configured `base_url` (`PFACTORY_AIFACTORY_API_URL`), never a caller-supplied callback URL.

4. `apps/backend/plan/service.py:99-195,928-966,2229-2441`: add `provider: str = "github"`, `project_id: str | None = None`, and `stage_state: dict[str, Any] | None = None` to `PlanSession`; add `find_active_issue_session(tenant_id, provider, repo, issue_number)` for durable deduplication; add `bootstrap_issue_stage(session_id)` and `poll_issue_stage(session_id)` guarded by `self._emit_lock(session_id, live=True)` (`service.py:2175-2228`) and persisted via `self._save(session)` (CAS in `PlanSessionStore.upsert`, `apps/web-server/server/jobstore/plan_session_store.py:111-168`); pass `session.stage_state` handoff fields into `_emit_contract` when `/approve plan` completes. On `/approve plan`, call `approve` with the W0 provenance `auth_kind="provider_review"` plus `provider`, immutable `provider_user_id`, `username`, `pr_number`, `approved_head_sha` (rebase on dev after PFactory#1095 merges).
   → verify by `apps/backend/.venv/bin/pytest tests/test_issue_stage_machine.py -k "service_stage" -v`
   Traps: `PlanSession` must stay importable in the dependency-light backend venv (do not import FastAPI/SQLAlchemy at top level in `service.py`); preserve `StaleSessionError` propagation in `_save` (`service.py:780-824`).

5. `apps/web-server/server/routes/plan_pipeline.py:80-100,340-377,854-886`, `apps/web-server/server/background/stage_poller.py:1-90` (new), and `apps/web-server/server/main.py:157-221`:
   - In `FromIssueBody` (`plan_pipeline.py:80-100`), add optional `base_ref: str | None = None`, `base_branch: str | None = None`, `project_id: str | None = None`, and `auto_start_stage: bool = True`.
   - In `ingest_from_issue` (`plan_pipeline.py:340-377`), return existing active session if `(tenant, provider, repo, issue_number)` matches, otherwise ingest and schedule `SERVICE.bootstrap_issue_stage` via `server.background.tasks.spawn` (`apps/web-server/server/background/tasks.py:55-60`).
   - Add `POST /api/plan/sessions/{session_id}/poll-stage` in `plan_pipeline.py` (offloaded via `asyncio.to_thread(SERVICE.poll_issue_stage, session_id)` after `_guard_session`).
   - Wire `stage_poller_loop` in `server/main.py:189-214` lifespan (alongside `stale_ingest_sweep_loop`).
   → verify by `apps/backend/.venv/bin/pytest apps/web-server/tests/test_e2e_workflows.py tests/test_issue_stage_machine.py -v`
   Traps: `_StrictBody` has `extra="forbid"` (`plan_pipeline.py:62-65`), so every field AIFactory's poller sends (`base_branch`, `project_id`, `change_mode`, `autonomy_tier`) must be declared on `FromIssueBody`; never use bare `asyncio.create_task` for fire-and-forget work (`AIFactory#1484` — always use `spawn`); never leak raw exception text in HTTP responses (`Factory#718`, use `client_error(exc)`).

6. Decision: do NOT edit the vendored `apps/backend/runners/github/` (drift gate); the hub fix in Factory `shared/factory-github/providers/gitlab_provider.py` belongs to W2b (AIFactory#2173). Here: exact-id GitLab lookup inside `apps/backend/plan/stage_policy.py` via the provider's API call, plus `tests/test_issue_stage_machine.py:1-240` (new): update GitLab permission resolution so `check_permissions` (and user-id lookup) resolves exact user ID via `/api/v4/projects/{self._project_id}/members/all/{user_id}` (or exact `u["username"].lower() == username.lower()` match before querying `/members/all/{id}`), never `params={"query": username}` + `resp.json()[0]`; add full unit coverage for all stage transitions, security rejections, SHA invalidation on new push, and handoff payload.
   → verify by `apps/backend/.venv/bin/pytest tests/test_issue_stage_machine.py -v && ruff check apps/ tests/`
   Traps: `apps/backend/runners/github/` is guarded by `.github/workflows/factory-github-drift.yml:37` (`HUB_SHA`); if the hub commit is not bumped in the same PR, keep the exact-id `/members/all/{user_id}` call in `apps/backend/plan/stage_policy.py` via `provider.api_get` so `runners/github/` stays byte-identical until the hub pin bumps.

## Tests

```bash
apps/backend/.venv/bin/pytest tests/test_issue_stage_machine.py -v
ruff check apps/backend/plan/ apps/web-server/server/ tests/test_issue_stage_machine.py
ruff format --check apps/backend/plan/ apps/web-server/server/ tests/test_issue_stage_machine.py
```

Expected result: all tests in `tests/test_issue_stage_machine.py` pass (covering idempotent `from-issue` pickup, base-branch `.factory.yml` + `CODEOWNERS` enforcement, immutable user-id check, single-use `(pr_number, stage, head_sha)` binding, invalidation on new push, `intent` → `spec` → `plan` → signed contract handoff with `source_branch`/`approved_head_sha`/`pr_number`, no auto-merge, and exact GitLab member lookup); `ruff check` and `ruff format --check` exit 0.

## Rollback

Revert the PR. `PlanSession` fields (`provider`, `project_id`, `stage_state`) and Task Contract `handoff`/`provenance` keys are additive and default to `None`, so existing sessions in `plan_sessions` and non-stage `emit-contract` calls continue working without migration rollback. Setting `APP_STAGE_POLLER_ENABLED=false` disables background PR comment polling immediately without a redeploy of the schema.

## Deviations (recorded during implementation)

- Step 1 also added the step-6 focused tests early, and the exact GitLab member lookup (`/members/all/{user_id}`) lives in `stage_policy.py` as decided; step 6 needed no separate handoff. The vendored `runners/github/` provider and the Factory hub fix stay for W2b (AIFactory#2173).
- CODEOWNERS team owners (`@org/team`) are not matched yet: such paths fail closed (no one can approve) until a team lookup is added.
- Step 5 adds an atomic first-time claim so concurrent `from-issue` calls across replicas create one session (review finding on step 4). The poller setting is `STAGE_POLLER_ENABLED` (env `APP_STAGE_POLLER_ENABLED`), default on, interval `STAGE_POLLER_INTERVAL_SECONDS`.
