# 2026-05-27 — Delegation epic + portal-managed clones epic shipped

> A single-session work log covering two major epics (#92 + #82) shipped end-to-end, plus
> the test/QA work and small infrastructure improvements that landed alongside them.

## What shipped

**18 feature/fix PRs merged to `dev`**, across two complete epics.

### Epic #92 — Delegation (Copilot + Duo) — Closed

| Piece | PR | Title |
|---|---|---|
| V1-A | #139 | `assign_to_user` Protocol + GitHub GraphQL impl |
| V1-B | #140 | Auto-Fix delegation branch + tracker + formatter |
| V1-C | #141 | Frontend toggles + "Delegated to Copilot" badge |
| V1-D | #142 | Delegation concept docs page |
| V1.5 | #151 | GitLab Duo Workflow delegation |
| hotfix | #143 | GHClient method name (use `run()` not `_run_gh_command`) |
| follow-up | #145 | 3 gaps + dedupe (#144) — planner-await, default-injection, wizard wiring |
| hotfix | #147 | Remaining `_run_gh_command` calls in merge_pr/close_pr/etc. |
| hotfix | #148 | run.py accepts `--remote-control` + per-spec spawn-stderr capture (#146) |
| follow-up | #150 | Real Remote Control SDK wiring via `extra_args` (#149) |

**Net outcome.** Both `enableDelegation` (per-task) and `delegateByDefault` (per-project)
work end-to-end for GitHub Copilot **and** GitLab Duo. The tracker promotes delegated
tasks to `in_review` when a matching bot-authored PR/MR appears, declines after 24 h
with no match. Comment deduping prevents double-posting on retries. Remote Control
toggle now genuinely registers a claude.ai/code session (was a silent no-op).

### Epic #82 — Portal-managed Git clones — Closed

| Piece | PR | Title |
|---|---|---|
| PR-A | #152 | Schema + clone service + `ProjectCreate.gitUrl` |
| follow-up | #153 | `/handover` auto-registers cwd via `gitUrl` when no project matches |
| PR-B backend | #155 | Helm `workspaces` PVC + Auto-Fix `pull-on-poll` |
| PR-B frontend | #156 | AddProjectModal "Clone from Git URL" mode |
| PR-C | #157 | `git_credentials` table + clone-service wiring |
| follow-up | #158 | Settings → Git Credentials page (mint/list/revoke PATs) |

**Net outcome.** SaaS/K8s deployments are first-class. The portal can clone any repo
(public + private with stored PATs) into a workspace root that defaults to
`~/.pfactory/workspaces/` on laptop installs and to a Helm-templated PVC on K8s.
`git pull --ff-only` runs on every Auto-Fix poll cycle so the planner sees the latest
commits. All of it is surfaced in the UI (wizard + Settings → Git Credentials).

## What got filed for later

**Issue #154** — *"v1.1 — MCP control-plane RBAC: scope-gated `acw_` keys for stdio
MCP"*, added as a sub-issue of Epic #35 (Enterprise v1.1). Includes full file:line
citations for the implementation when picked up.

**Issues #146 + #149** — both filed and closed within the session; their fixes
shipped in PR #148 and PR #150 respectively.

## Architecture decisions worth remembering

### Delegation is hybrid-only

PFactory's planner phase **always runs locally** on Claude before delegating the
coder phase. This burns a small amount of Claude tokens (~$5 / month for 50 delegated
tasks at Sonnet) but means Copilot/Duo get an enriched plan as a structured GitHub
issue comment before they start, not just the raw issue body. The decision is locked
in epic #92's "Decisions locked" table.

### Workspace root is environment-aware

`PROJECT_WORKSPACE_ROOT` env var controls where cloned repos land. Defaults to
`~/.pfactory/workspaces/` on laptop installs (zero config). Helm-templated PVC at
`/var/lib/pfactory/workspaces/` on K8s installs (PVC enabled via
`workspaces.enabled: true`). The clone service resolves the path the same way in
both environments.

### Stored credentials never leak to `.git/config`

When `clone_or_update(credential=...)` is called with a stored PAT, the token is
injected into the HTTPS URL **for the network operation only**, then immediately
sanitized via `git remote set-url origin <clean-url>` so the credential never
persists in the workspace's `.git/config`. Tokens are encrypted at rest in the
`git_credentials` table via the existing `EncryptedString` SQLAlchemy type from
Epic #26 P2.3.

### Two MCP servers, two auth postures

The **stdio MCP server** (`apps/backend/mcp_server/`) uses the legacy admin token at
`~/.pfactory/.token`. The **remote HTTP+SSE MCP server**
(`apps/web-server/server/mcp_remote/`) uses scoped `acw_` keys from the `ApiKey`
table. Issue #154 covers unifying the stdio server onto the same `acw_` model — when
that lands, the stdio MCP will be safe for shared-host enterprise deployments.

## Test / lint posture at end of session

- **Full suite: 2213 pass** (was ~2153 at session start → **+60 net** from new tests)
- **Same 24 pre-existing failures** — no new regressions introduced by any merged PR.
  Suites affected: `test_p0_docs`, `test_p7_evidence`, `test_thinking_level_validation`,
  `test_qa_criteria`, `test_github_pr_review`, `test_cache_blocks`, `test_obs_p6_obs`.
  Worth a dedicated hygiene pass when there's time.
- All touched files clean for `ruff` (Python) and `tsc --noEmit` (TypeScript).

## State of major open epics

| Epic | State | Notes |
|---|---|---|
| ~~#92 Delegation~~ | Closed | |
| ~~#82 Portal clones~~ | Closed | |
| #35 Enterprise v1.1 | Open | 9 children including #154 added this session |
| #50 MCP Control-Plane Tools | Open | Not touched this session |
| #100 Default MCP servers | Open | Helm chart already has `mcpCredentials:` block from earlier work — may be partially done; survey before picking up |

## Loose ends to look at before next session

### Dormant Duo workflow record on `compliance-calitii`

Stray workflow `id=3988061` created during early Duo API probing. Live record but
`status: created`, `project_id: null`, no executor attached — it's idle and not
consuming credits. API-side cleanup is 403-locked for the creating user (real GitLab
permission bug; the creator can POST a workflow but can't read/modify/delete it
once created). A group owner of `compliance-calitii` can drop it via the UI; otherwise
GitLab housekeeping will TTL it eventually.

### Manual smoke testing deferred

- **V1 Copilot delegation** — smoke-tested live during the session against
  `olafkfreund/pfactory-demo` issue #15 (PR-B + #150 verified end-to-end with a
  populated plan comment + Copilot assignment).
- **V1.5 GitLab Duo delegation** — unit-tested only. Live verification needs a Duo
  Pro/Enterprise add-on on a token the portal can use. The verified API surface
  matches what we proved against `gitlab.com` live during the V1 smoke test (see
  the #92 comment trail).
- **Portal-managed clones end-to-end** — unit-tested + the path is identical to what
  `mcp__pfactory__project_create` (PR #153) exercises today. The wizard's "Clone
  from Git URL" mode would benefit from a manual run against a real repo when
  someone has the chance.

### `NODE_ENV=production` in shell env

The shell environment carries `NODE_ENV=production`, which makes `npm install` skip
devDependencies. Caused one painful diagnostic detour mid-session (broken Vite). Worth
unsetting in `~/.zshrc` or remembering to prefix dev installs with `NODE_ENV=development`.

## File:line bookmarks (handy to keep)

- Delegation runner (shared helper used by Auto-Fix and the wizard's task-start route):
  `apps/web-server/server/services/delegation_runner.py`
- GitHub Copilot assignment (GraphQL `replaceActorsForAssignable`):
  `apps/backend/runners/github/providers/github_provider.py` — `assign_to_user`
- GitLab Duo Workflow assignment (`POST /api/v4/ai/duo_workflows/workflows`):
  `apps/backend/runners/github/providers/gitlab_provider.py` — `_trigger_duo_workflow`
- Clone service (portal-managed clones, credential injection):
  `apps/web-server/server/services/project_workspace_service.py`
- `git_credentials` model + encrypted-at-rest token column:
  `apps/web-server/server/database/models.py` — `GitCredential`
- Stdio MCP HTTP client (where the legacy admin token gets read — #154 will rework):
  `apps/backend/agents/tools_pkg/http_client.py:81-106`
- REST auth middleware (the dual-token gate JWT + legacy):
  `apps/web-server/server/auth.py:48-162`
- `acw_` key validator (the proper RBAC machinery, currently only used by remote MCP):
  `apps/web-server/server/mcp_remote/auth.py`

## Commit list (chronological, this session)

```
6aac3ef feat(frontend): Git Credentials settings page (epic #82 follow-up) (#158)
6f1ec13 feat(workspaces): git_credentials table + clone-service wiring (#82 PR-C) (#157)
8457f23 feat(frontend): AddProjectModal "Clone from Git URL" mode (epic #82 PR-B) (#156)
77e674a feat(workspaces): Helm PVC + pull-on-poll for portal clones (epic #82 PR-B) (#155)
1714f33 feat(handover): auto-clone cwd via gitUrl when no project matches (#153)
a33cd76 feat(projects): portal-managed git clones (epic #82, PR-A) (#152)
9202d6a feat(delegation): GitLab Duo Workflow delegation (V1.5, #98) (#151)
01e514d fix(remote-control): thread session name through SDK extra_args (#149) (#150)
a82705a fix(agent): accept --remote-control in run.py + capture spawn stderr (#146) (#148)
0351ccc fix(providers): use GHClient.run() in merge_pr/close_pr/create_issue/close_issue/create_label/list_labels (#147)
7c00106 fix(delegation): close 3 V1 gaps + dedupe enrichment comments (#144) (#145)
cf757ab fix(providers): use GHClient.run(), not the non-existent _run_gh_command (#143)
7513215 docs: delegation concept page (V1-D, #96) (#142)
c18b05b feat(frontend): Copilot delegation toggles + delegated badge (V1-C, #95) (#141)
ddad760 feat(auto-fix): Copilot delegation flow end-to-end (V1-B, #94) (#140)
341ea97 feat(providers): add assign_to_user for Copilot delegation (V1-A, #93) (#139)
```
