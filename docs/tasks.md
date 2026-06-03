---
layout: default
title: Task Breakdown
permalink: /tasks/
nav_order: 6
---

# Spec Tasks

> **Historical record — v0.1.0-mvp planning checklist (2026-05-28).**
> All 12 tasks below shipped in [v0.1.0-mvp](https://github.com/olafkfreund/PFactory/releases/tag/v0.1.0-mvp).
> The v0.2 epic that built on top also shipped in
> [v0.2.0](https://github.com/olafkfreund/PFactory/releases/tag/v0.2.0)
> (16 / 16 tasks closed). See [Progress]({{ '/progress/' | relative_url }})
> for the live status dashboard and the [v0.2.0 demo]({{ '/showcase/' | relative_url }})
> for the released pipeline running end-to-end.

These are the tasks that were planned in the spec at `@.agent-os/specs/2026-05-28-pfactory-mvp-walking-skeleton/spec.md`.

> Created: 2026-05-28
> Status: **Shipped — v0.1.0-mvp**
> Source design: `/home/olafkfreund/.claude/plans/virtual-cooking-bumblebee.md`
> Approach: TDD throughout — write tests first, implement, verify green.

## Conventions

- Each numbered task is a meaningful unit of work that ends in a commit.
- Sub-tasks `.1`, `.2`, ... are executed in order; the first is always "write tests" and the last is always "verify all tests pass".
- After every numbered task, run the full unit + integration test suites and confirm green before starting the next.
- Mark `[~]` for in-progress, `[x]` for done, `[!]` for blocked (with `⚠️` reason).

## Tasks

- 1. **Scaffold PFactory by hard-forking AIFactory**
  - 1.1 Write a sanity check script `scripts/verify-fork.sh` that asserts: PFactory dir exists, all key Python modules importable, no `aifactory` string remains except in documented intentional cross-references
  - 1.2 `cp -r /home/olafkfreund/Source/GitHub/AIFactory/. /home/olafkfreund/Source/GitHub/PFactory/` (preserves `.agent-os/specs/2026-05-28-pfactory-mvp-walking-skeleton/` already present)
  - 1.3 Delete obsolete files from the fork per the design plan's "Files to delete from the fork" list (coder.py + coder_*, qa/, spec_runner.py, spec_agents/, coder*.md prompts, qa_*.md prompts, complexity_assessor.md, followup_planner.md, .claude/skills/aifactory-spec/, .claude/skills/handover/, runners/github/ PR-merge code, frontend wizard + plan-approval components)
  - 1.4 Rename references: `aifactory` → `pfactory` across module paths, env vars, scripts, port defaults, `.mcp.json`; rename `apps/backend/mcp_server/aifactory_server.py` → `pfactory_server.py`; rename module `apps/backend/implementation_plan/` → `apps/backend/test_plan/`
  - 1.5 Initialize PFactory as a git repo, first commit "pfactory: initial fork from AIFactory @ <sha>"
  - 1.6 Run `scripts/verify-fork.sh`; resolve any failures
  - 1.7 Verify all tests pass (inherited AIFactory tests not relevant to deleted modules; remove or skip those; the remainder must pass)

- 2. **MCP server + handover skill**
  - 2.1 Write tests for `pfactory_server.py`: tool listing, `task_create_and_run` happy + unhappy paths, `task_status`, `task_rerun` lane gate, `report_get` markdown + json (per tests.md, "MCP server" unit tests)
  - 2.2 Implement `apps/backend/mcp_server/pfactory_server.py` with the six MVP tools listed in technical-spec.md
  - 2.3 Update `.mcp.json` to register the new server via `scripts/start-pfactory-mcp.sh`
  - 2.4 Write the `/handover-to-pfactory` Claude Code skill: PFactory-side at `PFactory/.claude/skills/handover-to-pfactory/SKILL.md` AND companion at `AIFactory/.claude/skills/handover-to-pfactory/SKILL.md` (the user-facing one — only this one is invoked from Claude Code)
  - 2.5 Manual smoke: in a Claude Code session in AIFactory, run `/handover-to-pfactory --spec <fixture-spec-id>` and verify the MCP call is dispatched correctly (mock the backend response)
  - 2.6 Verify all tests pass

- 3. **Workspace + state model**
  - 3.1 Write tests for `test_plan/` model (round-trip, lane validation, status transitions) and for the `context/source.json` snapshot module (per tests.md)
  - 3.2 Rename the implementation plan module to `test_plan/`; add `lane` field to Subtask; update all references in the fork
  - 3.3 Implement `apps/backend/workspaces/snapshotter.py`: reads AIFactory spec dir read-only, copies `spec.md` + `implementation_plan.json` into `context/`, writes `context/source.json` with metadata, marks copies mode 0o444, computes `context/diff.patch`
  - 3.4 Wire the worker to call snapshotter on `task_create_and_run` before any agent runs; assert workspace layout matches `technical-spec.md`
  - 3.5 Verify all tests pass

- 4. **Docker runner + base image**
  - 4.1 Write tests for `docker_runner.py`: command construction (network=none, read-only, mounts, limits), JUnit/coverage round-trip, timeout behavior, podman-rootless fallback (skipped if absent)
  - 4.2 Author `docker/runners/python.Dockerfile`; `docker build -t pfactory-runner-python:latest` succeeds
  - 4.3 Implement `apps/backend/tools/runners/docker_runner.py` and the native pass-through stub
  - 4.4 Wire executor to dispatch by lane: `functional` → docker, `sast` → native pass-through stub raising "not implemented at MVP"
  - 4.5 Run an end-to-end Docker smoke: a hand-written pytest file inside a fixture project executes in the container, JUnit + coverage XML come back, test result parses
  - 4.6 Verify all tests pass

- 5. **Planner agent**
  - 5.1 Write tests for `planner.py` against a fixture `aifactory_spec.md` + `diff.patch` (mocked LLM); verify replan behavior; verify "stuck after 2 replans" path
  - 5.2 Author `apps/backend/prompts/planner.md` (testing-oriented; reads acceptance criteria + diff; emits lane-tagged subtasks)
  - 5.3 Implement `apps/backend/agents/planner.py` (Claude Agent SDK session, JSON output schema enforced, replan reentry supported)
  - 5.4 Add `lane: functional` filter so MVP planner only emits functional subtasks even if the prompt suggests others
  - 5.5 Verify all tests pass

- 6. **Gen-Functional agent (Python)**
  - 6.1 Write tests for the pre-flight static check (hallucinated import, hallucinated method, valid case) using a subprocess-based introspection helper
  - 6.2 Write tests for the flake-risk lint (each pattern: positive + negative case)
  - 6.3 Author `apps/backend/prompts/gen_functional.md` (pytest-focused, allow-listed API exposure rules)
  - 6.4 Implement `apps/backend/agents/gen_functional.py` with both guards integrated, replan request on rejection
  - 6.5 Integration test: planner → gen_functional happy path against a fixture project, files written to `tests/functional/` in workspace
  - 6.6 Verify all tests pass

- 7. **Evaluator agent**
  - 7.1 Write tests for coverage-delta computation, 3x stability re-run, mutate-and-check probe (with fixed seed), LLM semantic relevance (mocked) → verdict pipeline (per tests.md "Evaluator")
  - 7.2 Author `apps/backend/prompts/evaluator.md`
  - 7.3 Implement `apps/backend/agents/evaluator.py`
  - 7.4 Integration test: full chain planner → gen_functional → executor → evaluator on a fixture, with a deliberately trivial test (mutate-probe must reject it) and a real test (must be accepted)
  - 7.5 Verify all tests pass

- 8. **Triager + git writer + PR comment**
  - 8.1 Write tests for dedup (byte-identical and whitespace-normalized), rank ordering, golden-file snapshot of `report.md`, git command construction (dry-run), `gh pr comment` argv (dry-run)
  - 8.2 Author `apps/backend/prompts/triager.md`
  - 8.3 Implement `apps/backend/agents/triager.py` and `apps/backend/tools/git_writer.py`
  - 8.4 Trim AIFactory's `runners/github/` to a single small `pr_comment.py` helper; delete the PR-merge workflow
  - 8.5 Integration test: full chain through triager writes report files and produces correct git/gh commands in a temp clone of a fixture repo
  - 8.6 Verify all tests pass

- 9. **Portal retheme (backend)**
  - 9.1 Write tests for new task endpoints: `/tasks`, `/tasks/<id>`, `/tasks/<id>/report.{md,json}`; WebSocket `/tasks/<id>/logs/stream` connection lifecycle
  - 9.2 Remove spec-creation routes from `apps/web-server/main.py`; add task endpoints
  - 9.3 Configure backend port to `:3102` (env-driven, default in `.env.example`)
  - 9.4 Verify all tests pass

- 10. **Portal retheme (frontend)**
  - 10.1 Write component tests for: task list table, single-task view tabs, lane status grid (placeholders for un-lit lanes), report markdown viewer
  - 10.2 Delete spec wizard, plan-approval UI, follow-up planner components from `apps/frontend-web/`
  - 10.3 Add lane status grid with `functional` lit and `sast/dast/fuzz/mutation` showing "coming in Phase N" placeholders
  - 10.4 Wire WebSocket live-log viewer reused from AIFactory inventory
  - 10.5 Manual smoke: start the portal, kick a fixture task, watch progression in the UI
  - 10.6 Verify all tests pass

- 11. **End-to-end smoke (the 9 verification scenarios)**
  - 11.1 Write `scripts/e2e-smoke.sh` running the 9 scenarios from the design plan
  - 11.2 Scenario 1: pick a known small AIFactory-produced Python spec; check workspace creation
  - 11.3 Scenarios 2-3: start portal; trigger handover; observe progression
  - 11.4 Scenarios 4-5: verify generated tests are committed; `pytest tests/` passes
  - 11.5 Scenario 6: mutate one line of feature code; rerun tests; at least one fails
  - 11.6 Scenario 7: verify PR comment via `gh pr view --comments`
  - 11.7 Scenario 8: hallucination guard — feed planner a spec referring to a non-existent method; verify Gen-Functional rejects and Planner replans, no broken test committed
  - 11.8 Scenario 9: docker-daemon-down failure path — task marked `failed` with clear error; no hang
  - 11.9 Document any flake or sharp edge for the phase-2 backlog
  - 11.10 Verify all tests pass

- 12. **Documentation + handoff**
  - 12.1 Update `PFactory/CLAUDE.md` with PFactory-specific guidance (mirrors AIFactory's CLAUDE.md shape, content rewritten for the test-generation domain)
  - 12.2 Update `PFactory/README.md` with quickstart: install deps, `docker build`, `scripts/start-pfactory-portal.sh`, run a handover, view results
  - 12.3 Update `PFactory/.env.example` with all new env vars (`PFACTORY_WORKSPACE_ROOT`, `PFACTORY_DOCKER_IMAGE_PYTHON`, `PFACTORY_PORTAL_PORT`, `PFACTORY_TASK_TIMEOUT_SEC`)
  - 12.4 Document the AIFactory-side `handover-to-pfactory` skill in AIFactory's docs
  - 12.5 Tag `v0.1.0-mvp` after `scripts/e2e-smoke.sh` is green end-to-end

## Dependency graph (rough)

```
1 (scaffold)
  ├─→ 2 (mcp + skill)
  ├─→ 3 (state model)
  └─→ 4 (docker runner)
        │
   ┌────┴────┬─────────┐
   │         │         │
   v         v         v
   5 (planner)   6 (gen_functional)
        │         │
        └────┬────┘
             v
             7 (evaluator) ── requires 4 + 6
             │
             v
             8 (triager + git)
             │
             v
       ┌──── 9 (portal backend)
       │     │
       │     v
       │     10 (portal frontend)
       │     │
       └─────┴───→ 11 (e2e smoke)
                       │
                       v
                       12 (docs)
```

Tasks 2, 3, 4 can be executed in parallel after 1. Tasks 5 and 6 can be parallel after 3+4. Everything else is serial.

## Stop conditions

- If any sub-task fails 3 attempts, mark the parent `[!]` with `⚠️ <reason>` and surface to the user instead of looping further.
- If the design plan's spec schema assumptions break (e.g., AIFactory ships a backward-incompatible spec format change), stop and refer back to the design plan's risk register entry "AIFactory spec schema changes break PFactory" — re-snapshot from the snapshotted contract is the right escape.

## Out of scope (reminder, see spec.md for full list)

- Mutation, SAST, DAST, fuzz lanes
- TypeScript and other-language runners
- E2B / Firecracker isolation
- Cross-task findings triage view
- factory-core shared library extraction
