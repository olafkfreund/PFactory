---
layout: default
title: "Spec — MVP Walking Skeleton"
permalink: /spec/
nav_order: 3
---

# Spec Requirements Document

> ⏳ **Historical record — v0.1.0-mvp spec snapshot (2026-05-28).** Documents
> the v0.1 Functional-lane MVP. The shipped product moved to the v0.2 modality
> spine (unit / browser / api / integration / mutation) and dropped security
> lanes from scope — see [Architecture](/architecture/) + the repo README for
> current state.

> Spec: PFactory MVP — Walking Skeleton (Functional Lane, Python)
> Created: 2026-05-28
> Status: Planning
> Source design: `/home/olafkfreund/.claude/plans/virtual-cooking-bumblebee.md` (approved 2026-05-28)
> Sister project: `/home/olafkfreund/Source/GitHub/AIFactory`

## Overview

Ship the first end-to-end PFactory pipeline: receive a completed AIFactory spec via `/handover-to-pfactory`, generate Python pytest tests aligned to the spec's acceptance criteria, execute them in a Docker sandbox, evaluate quality, commit the tests to the feature branch, and post a coverage report as a PR comment. Proves the six-agent architecture (shared Planner → per-lane Generators → shared Executor / structurally-separate Evaluator → Triager) end-to-end, with only the functional lane lit. SAST / DAST / fuzz / mutation lanes and TypeScript come in later phases.

## User Stories

### Hand a finished feature off to PFactory

As a developer using AIFactory, after AIFactory's Coder + QA finish a Python feature on a draft PR, I want to run `/handover-to-pfactory` and have PFactory generate aligned tests + a coverage report without me writing them by hand, so that the same PR ships with both feature + tests in one review cycle.

### Review a single PR with feature + tests

As a code reviewer, I want PFactory's generated tests committed on the same feature branch as the feature itself and a clear report posted as a PR comment, so that I can review the feature, its tests, and their coverage in one place instead of juggling multiple PRs or external dashboards.

### Trust the generated tests

As a developer trusting AI-generated tests, I want PFactory to reject hallucinated imports and missing-method calls before commit, and to lint for common flakiness patterns (unordered-collection assertions, timing assumptions), so that I don't waste review cycles on tests that import nonexistent symbols or fail intermittently in CI.

### Watch a long-running task

As a developer who just kicked off a PFactory task, I want a portal at `:3102` that shows live status (pending → planning → generating → running → evaluating → triaging → done) with per-agent logs, so that I can see progress and debug failures without grep'ing files in `~/.pfactory/workspaces/`.

## Spec Scope

1. **Hard-fork scaffold** — `cp -r AIFactory PFactory`, delete code-generation-specific modules (coder agent, QA agent, spec-creation pipeline, GitHub PR-merge runner), rename MCP server and ports.
2. **`/handover-to-pfactory` skill + MCP server** — Claude Code skill in `.claude/skills/handover-to-pfactory/SKILL.md` calling `mcp__pfactory__task_create_and_run` with `{ project_id, spec_id, branch, base_ref }`; PFactory snapshots AIFactory's spec dir read-only into its own workspace.
3. **PFactory workspace + state model** — `~/.pfactory/workspaces/{project_id}/specs/{spec_id}/` layout with `task.md`, `test_plan.json`, `context/`, `tests/`, `findings/`, `report.{md,json}`, `logs/`, `memory/`.
4. **Planner agent** — reads `context/aifactory_spec.md` + `context/diff.patch` + acceptance criteria, emits `test_plan.json` with lane-tagged subtasks (only `functional` lit at MVP).
5. **Gen-Functional agent (Python)** — generates pytest tests with pre-flight static check (every import + method call resolves against the project's actual symbols) and flake-risk lint (unordered-collection assertions, timing assumptions); allow-listed API exposure scoped to diffed code + direct deps' public API.
6. **Docker executor** — `pfactory-runner-python` image (Debian slim + Python 3.12 + pytest + coverage), repo bind-mounted read-only, scratch volume writable, network off; per-lane dispatch in shared executor module with native pass-through for SAST (interface placed even though SAST is phase 3).
7. **Evaluator agent (structurally separate)** — coverage delta, flake-lint scoring, 3x stability re-run, LLM semantic relevance judgment, sanity "mutate-one-feature-line → expect at least one test fails" probe; emits per-test verdict (accept / reject / flag) + rationale.
8. **Triager + git side-effects** — dedup, rank, render `report.md` + `report.json`; `git commit` accepted tests under the project's test directory on the AIFactory feature branch with prefix `pfactory:`; `gh pr comment <pr> --body $REPORT`.
9. **Portal retheme** — FastAPI on `:3102`, React on companion port; routes `/tasks`, `/tasks/<id>` (lane tabs with `functional` lit, others showing "coming in Phase N"), `/projects`; reuse layout shell, auth, task list table, WebSocket live-update infra, log viewer.

## Out of Scope

- **Other test lanes at MVP**: SAST + dep CVE + secrets (phase 3), mutation testing (phase 2), DAST + fuzzing (phase 5). Generator stubs and prompt files may be created as empty placeholders if cheap; their wiring is not.
- **Non-Python targets**: TypeScript (phase 4), Go / Rust / Ruby (phase 6+). No `node` Docker runner image at MVP.
- **PFactory-side spec creation**: PFactory consumes AIFactory specs; it does not create its own. The AIFactory `spec_runner.py` / `spec_agents/` modules are deleted from the fork.
- **Cross-task findings triage view** in the portal — punted to phase 3 when SAST gives findings worth aggregating.
- **Stronger isolation than Docker**: no Firecracker / E2B at MVP.
- **Audit log + cost reporting** — noted gap, deferred.
- **`factory-core` shared library extraction** — accepted fork-drift trade-off per design decision #1.
- **Auto-chaining from AIFactory's `/handover`** — the `--also-test` flag on AIFactory's handover is a nice-to-have, not in MVP scope; user explicitly invokes `/handover-to-pfactory`.

## Expected Deliverable

1. **End-to-end smoke passes**: running `/handover-to-pfactory --spec <id>` on a known small Python feature (a recent AIFactory output) results in a `pfactory: tests for <spec_id>` commit on the feature branch containing pytest files under the project's test dir, and a PR comment with a coverage-delta + flake-lint summary report. The 9 verification scenarios in the design plan all pass, including the hallucination guard scenario (#8) and the docker-down failure-path scenario (#9).
2. **Portal at `:3102` shows live task progression**: opening `/tasks/<id>` during a run displays the task transitioning through `pending → planning → generating → running → evaluating → triaging → done` with live agent logs streaming via WebSocket; the functional-lane tab shows the planner's subtask list, per-subtask generation status, and the evaluator's per-test verdicts; SAST / DAST / fuzz / mutation tabs are visible but disabled with "coming in Phase N" labels.
3. **Hallucination + flake guards demonstrably work**: feeding the planner a spec that references a method that doesn't actually exist in the diff causes Gen-Functional to detect the missing symbol in pre-flight, reject the generation, and request a replanned subtask from the planner rather than commit a broken test. Feeding the evaluator a generated test that asserts on dictionary iteration order causes the flake-lint to flag it and the evaluator to reject or downgrade it.
