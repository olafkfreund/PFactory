---
status: approved
issue: 1096
author: olafkfreund
---

# Intent: issue → intent/spec/plan stage machine on a draft PR with bound /approve

## Problem

When AIFactory's intake poller routes a `factory:hard` issue to PFactory (`POST /api/plan/sessions/from-issue`, `apps/web-server/server/routes/plan_pipeline.py:340-377`), PFactory only calls `SERVICE.ingest_text` and leaves the session in `ingested` status (`apps/backend/plan/service.py:928-966`). Nothing auto-processes it, opens a draft PR, or posts progress back to the issue, so hard-tier intake stalls silently. In addition:

- The artifact workflow (`intent/<slug>.md` → `spec/<slug>.md` → `plan/<slug>.md`) is not driven in the target repository from an issue, and no stage approval is reachable from GitHub/GitLab PR comments.
- `from-issue` accepts no `base_ref`, and AIFactory's `_route_hard` drops `cfg.base_branch` (`/mnt/code/Source-home/GitHub/AIFactory/apps/web-server/server/services/intake_poller.py:327-362`), losing the target integration branch (such as `dev`) and any `project_id`.
- Emitted Task Contracts (`apps/backend/plan/emit/contract_builder.py:197-266`) carry `repo`, `base_ref`, and `baseline_commit` in `provenance`, but carry no `source_branch`, `approved_head_sha`, or `pr_number` for AIFactory to adopt.
- GitLab's `check_permissions` (`apps/backend/runners/github/providers/gitlab_provider.py:781-799`, `/mnt/code/Source-home/GitHub/Factory/shared/factory-github/providers/gitlab_provider.py:755-774`) uses `params={"query": username}` and takes `resp.json()[0]`, which is a partial substring search rather than an exact immutable user-id lookup.

## Proposed outcome

- **Durable stage job & no hard-tier stall:** `POST /api/plan/sessions/from-issue` keys a durable PFactory stage job by `(tenant_id, provider, repo, issue_number)` (idempotent on re-delivery), reads `.factory.yml` from the trusted base branch (`base_ref` or default branch), auto-processes the session off the event loop, cuts branch `factory/<n>-<slug>` from the base ref, commits `intent/<slug>.md`, opens a draft PR against the base ref, labels the issue `factory:intent`, and posts a status comment linking the draft PR.
- **Bound, single-use `/approve <stage>` on the draft PR:** A background stage poller reads PR discussion via `fetch_pr_comments` (`apps/backend/runners/github/providers/protocol.py:614-639`), parses `/approve intent|spec|plan`, resolves the commenter by immutable provider user id (`raw_data["user"]["id"]` on GitHub; exact user-id member lookup `/members/all/{user_id}` on GitLab), and evaluates authorization exclusively from the **trusted base branch** (`CODEOWNERS` matching the stage file path `intent/<slug>.md`, `spec/<slug>.md`, or `plan/<slug>.md` when a rule covers it; else repository `write`/`admin` permission — explicit rule, not either-or).
- **Replay & push protection:** Each approval is bound to `(pr_number, stage, head_sha)` and consumed once; any new push to the PR branch changes `head_sha` and requires a fresh `/approve <stage>`.
- **Stage progression & handoff:**
  - `/approve intent` flips `intent/<slug>.md` to `status: approved`, commits `spec/<slug>.md` (`status: draft`) on `factory/<n>-<slug>`, and updates the issue label to `factory:spec`.
  - `/approve spec` flips `spec/<slug>.md` to `status: approved`, runs `PlanService.process` to produce and commit self-contained `plan/<slug>.md` (`status: draft`), and updates the issue label to `factory:plan`.
  - `/approve plan` flips `plan/<slug>.md` to `status: approved`, records the verified human approval (`PlanService.approve`), assembles and signs the RFC-0002 Task Contract v2 carrying the unified handoff identity (`project_id`, `provider`, `repo`, `base_ref`, `source_branch`, `approved_head_sha`, `pr_number`), POSTs it to AIFactory's configured `/api/tasks/from-plan` origin, and transitions the issue label to `factory:building` (or `factory:needs-human` on terminal gate/handoff failure).

## Affected users and systems

- **PFactory (this repo):** `plan_pipeline.py` (`from-issue` and stage job routes), new `apps/backend/plan/stage_machine.py` (`.factory.yml` + `CODEOWNERS` base-branch reader, stage transitions, bound approval verifier, git branch/commit/draft-PR driver), `plan/service.py` (`PlanSession` stage state), `plan/emit/contract_builder.py` and `contract_emit.py` (handoff identity fields), web-server background stage poller.
- **Shared provider layer (`Factory` hub + vendored copy):** `gitlab_provider.py` exact user-id permission check (gated by `.github/workflows/factory-github-drift.yml`).
- **Companion boundary (`olafkfreund/AIFactory#2172`):** PFactory owns the stage job, `.factory.yml` / `CODEOWNERS` evaluation on the base branch, draft PR commits, bound `/approve` consumption, and signed contract emission. AIFactory (`#2172`) owns forwarding `base_ref`/`project_id` in `_route_hard`, adopting the existing `source_branch`, `approved_head_sha`, and `pr_number` in `/api/tasks/from-plan`, sending `fix-completed` to the configured TFactory origin, and marking the draft PR ready for review (never auto-merging) after TFactory verifies the exact head SHA.

## Constraints

- **Approver identity & base-branch policy:** Never trust mutable display names or PR-branch policy files. Resolve the comment author by immutable provider user id; read `.factory.yml` and `CODEOWNERS` only from the trusted base branch (`base_ref`). If `CODEOWNERS` defines owners for the stage file path, only those owners may approve; otherwise require repository `write` or `admin` permission.
- **Single-use SHA binding:** An `/approve <stage>` comment is valid only when `stage` matches the job's current draft stage and the PR's current `head_sha` equals the commit PFactory pushed for that stage (or the latest commit on the PR at comment time). Consumed comment IDs and `(pr_number, stage, head_sha)` tuples are persisted durably and never reused.
- **Never auto-merge:** PFactory opens the PR as a draft (`is_draft=True`) and never calls `merge_pr` or `enable_auto_merge`.
- **Configured origins only:** Outbound handoff and completion callbacks go strictly to operator-configured origins (`PFACTORY_AIFACTORY_API_URL`, `PFACTORY_COMPLETION_WEBHOOK`), never to caller-supplied URLs.
- **Untrusted content:** Issue title/body, PR comments, and repo files are untrusted data (`Factory#273`); never execute target-repo code or git hooks (`core.hooksPath=/dev/null`, `git_run` pins in `apps/web-server/server/services/git_utils.py:70-91`).

## Open questions

- **GitHub team handles in `CODEOWNERS`:** When `CODEOWNERS` on the base branch names `@org/team-slug` for `intent/`, `spec/`, or `plan/`, should W2 resolve org team membership via `GET /orgs/{org}/teams/{slug}/memberships/{username}` (requires `read:org` token scope) or fail closed to explicit `@username` entries + repo `admin` when the token lacks `read:org`?
- **Hub drift pin for `gitlab_provider.py`:** `apps/backend/runners/github/providers/gitlab_provider.py` is byte-pinned to `olafkfreund/Factory` (`HUB_SHA` in `.github/workflows/factory-github-drift.yml:37`). Confirm that the exact-id lookup lands in `Factory` first and is re-vendored with a `HUB_SHA` bump in this PR (or in `W2b` `AIFactory#2173`).
