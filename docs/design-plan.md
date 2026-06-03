---
layout: default
title: Design Plan
permalink: /design-plan/
nav_order: 2
---

# PFactory Design Plan

> Date: 2026-05-28
> Author: design via super-brainstorm session
> Status: Design locked — ready for implementation planning

---

## Context

The user operates **AIFactory** at `/home/olafkfreund/Source/GitHub/AIFactory` — a
multi-agent autonomous coding platform built on the Claude Agent SDK. AIFactory's
`/handover` slash command hands an interactive Claude Code session over to an
async backend pipeline (Planner → Coder → QA → draft PR).

The user wants a sister platform, **PFactory** (Test Factory), purpose-built for
autonomous test generation and execution: functional, integration, e2e, plus
security testing (SAST, deps, secrets), dynamic testing (DAST, LLM fuzz harnesses),
and quality gating (mutation testing, property-based tests).

**Triggering workflow:** AIFactory's Coder finishes a spec → user runs
`/handover-to-pfactory` → PFactory receives the spec + completed branch, generates
tests aligned to acceptance criteria, scans the changed code for vulns, executes
everything sandboxed, triages, and commits tests + posts a findings report back
to the same PR.

---

## Findings from Phase 1 exploration

### AIFactory anatomy (the surface we're forking)

- **Pipeline:** Planner → Coder → QA, phase-bound, not a pluggable agent registry.
- **Skill commands** live at `.claude/skills/{name}/SKILL.md` — YAML frontmatter + procedural markdown.
- **`/handover` skill** at `.claude/skills/handover/SKILL.md` calls
  `mcp__aifactory__task_create_and_run`. The MCP server is at
  `apps/backend/mcp_server/aifactory_server.py`.
- **Spec dir** = single source of truth, at
  `~/.aifactory/workspaces/{project_id}/specs/{spec_id}/` with
  `spec.md`, `implementation_plan.json`, `context/`, `logs/`, `memory/`.
- **Implementation plan model** at `apps/backend/implementation_plan/models.py` —
  domain-agnostic phase/subtask containers, directly reusable.
- **Prompt assembly** at `apps/backend/prompts_pkg/` — loads markdown templates
  + injects project context; reusable.
- **Project analyzer** at `apps/backend/context/project_analyzer.py` — detects
  language, deps, test framework; reusable.
- **Command executor** at `apps/backend/tools/executor.py` — captures stdout/stderr;
  reusable.
- **MCP tool registry** at `apps/backend/agents/tools_pkg/registry.py` — pattern
  reusable.
- **QA agent** at `apps/backend/qa/qa_reviewer.py` and `qa_fixer.py` — runs tests,
  parses output, loops to coder. Closest existing analog to what PFactory needs.
- **Memory layer** at `apps/backend/agents/memory_manager.py` — Graphiti primary,
  file-based JSON fallback.
- **Provider abstraction** at `apps/backend/providers/` — OpenAI/Anthropic/Ollama/LocalAI.
- **Portal** at `apps/web-server/` (FastAPI) + `apps/frontend-web/` (React 19 + Vite).

### What needs real rework (not just renaming)

- `prompts/coder*.md` says "write code, implement feature, git commit" — PFactory
  needs entirely new agent personas (one per lane, plus planner/evaluator/triager).
- `apps/backend/runners/github/` assumes PR-merge workflow — PFactory's git
  side-effect is "add tests to existing branch + comment on PR".
- QA loop's acceptance criteria is "code matches spec" — PFactory's is
  "tests cover acceptance criteria + kill mutants + surface vulns".
- Spec creation pipeline (`spec_runner.py`, `spec_agents/`) is not needed in
  PFactory — PFactory consumes specs, doesn't create them. Delete or repurpose.

### Landscape signals (May 2026)

- Canonical multi-agent QA pattern (independently converged on by OpenAI + Anthropic):
  **Planner → Generator → Executor → Evaluator → Triager.** Evaluator structurally
  separate from generators — self-evaluation is unreliable.
- **Mutation testing is the only signal that filters trivial assertions**
  (Meta's TestGen-LLM, DiffBlue, recent PRIMG paper). Non-negotiable for trust.
- **Hallucinated tests are the #1 failure mode** — ~39% of LLM-generated Python
  test failures are structural (imports of nonexistent utilities, fake methods).
  Mitigation: pre-flight compile/import checks + allow-listed APIs.
- **Flakiness** in LLM-generated tests is higher than human-written; unordered
  collection assumptions are the leading cause. Mitigation: static lint pass on
  generated test code.
- **Security autonomy is winning** (XBOW, PentAGI, DARPA AIxCC, CodeMender).
  Functional gen is harder than security in 2026.

---

## Locked design decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Repo strategy | **Hard fork** — `cp -r AIFactory PFactory`, then surgically rework |
| 2 | Vision scope | **All four lanes** — functional, SAST+deps, DAST+fuzz, mutation+property |
| 3 | Handover payload | **Spec-aware** — `{project_id, spec_id, branch, base_ref}`; PFactory reads AIFactory's spec dir read-only |
| 4 | Agent topology | **Shared planner, per-lane generators, shared executor/evaluator/triager** (6 agent roles) |
| 5 | Execution env | **Tiered** — native for SAST, Docker per task for runtime lanes |
| 6 | MVP languages | **Python + TypeScript** for full vision; **Python only** at MVP cut |
| 7 | Deliverable | **Auto-commit tests to AIFactory's feature branch + PR comment with report** |
| 8 | Persistence | **`~/.pfactory/workspaces/{project_id}/specs/{spec_id}/`** mirroring AIFactory layout |
| 9 | Portal/UI | **Retheme inherited portal** at `:3102` — keep layout shell/auth/task list, drop spec-creation, add lane status + findings table + mutation gauge |
| 10 | MVP cut | **Walking skeleton** — functional lane only, Python only, full pipeline end-to-end |

---

## Architecture

### Component map

```
                            ┌─────────────────┐
                            │  AIFactory      │
                            │  finishes spec, │
                            │  pushes branch  │
                            └────────┬────────┘
                                     │
                  /handover-to-pfactory  (Claude Code skill)
                                     │
                                     v
                       mcp__pfactory__task_create_and_run
                                     │
                                     v
                            ┌────────────────┐
                            │ PFactory MCP   │
                            │ server :3103   │   (stdio)
                            └────────┬───────┘
                                     │
                                     v
                            ┌────────────────┐
                            │ PFactory web   │
                            │ backend :3102  │   FastAPI
                            └────────┬───────┘
                                     │ creates spec dir
                                     │ enqueues task
                                     v
                            ┌─────────────────┐
                            │  PLANNER agent  │
                            │  reads spec.md, │
                            │  diff, ac. crit │
                            └────────┬────────┘
                                     │ emits test_plan.json
                                     │ (lane-tagged subtasks)
                                     v
              ┌──────────┬───────────┼───────────┬──────────┐
              │          │           │           │          │
              v          v           v           v          v
        ┌─────────┐┌─────────┐ ┌─────────┐ ┌─────────┐
        │Gen-Func ││Gen-SAST │ │Gen-DAST │ │Gen-Mut  │
        │  pytest ││ semgrep │ │ ZAP +   │ │ mutmut  │
        │  (LLM)  ││ bandit  │ │ fuzz    │ │ harness │
        │         ││ deps    │ │ harness │ │         │
        └────┬────┘└────┬────┘ └────┬────┘ └────┬────┘
             │          │           │           │
             └──────────┴───────────┼───────────┘
                                    │
                                    v
                          ┌──────────────────┐
                          │    EXECUTOR      │ shared runner;
                          │ docker per task  │ native for SAST
                          └────────┬─────────┘
                                   │
                                   v
                          ┌──────────────────┐
                          │   EVALUATOR      │ scores quality:
                          │ structurally     │  - coverage delta
                          │ separate         │  - mutation score
                          │                  │  - severity / dedup
                          │                  │  - flake-risk lint
                          └────────┬─────────┘
                                   │
                                   v
                          ┌──────────────────┐
                          │    TRIAGER       │ dedup, rank, report
                          └────────┬─────────┘
                                   │
                                   v
        ┌──────────────────────────────────────────────────┐
        │  side-effects                                    │
        │   - git commit tests on AIFactory feature branch │
        │   - gh pr comment <pr> --body REPORT             │
        │   - write artifacts to ~/.pfactory/workspaces/   │
        │   - portal :3102 shows live status               │
        └──────────────────────────────────────────────────┘
```

### Spec dir layout (per task)

```
~/.pfactory/workspaces/{project_id}/specs/{spec_id}/
  task.md                 # handover payload, agent-readable
  test_plan.json          # planner output, lane-tagged subtasks
  context/
    source.json           # { aifactory_spec_dir, branch, base_ref, sha }
    aifactory_spec.md     # snapshot copy of AIFactory's spec.md (read-only)
    aifactory_plan.json   # snapshot copy
    diff.patch            # base_ref..branch
    project_analysis.json # languages, frameworks, deps
  tests/                  # generated test artifacts pre-commit
    functional/
    sec/
  findings/
    sast.json
    deps.json
    secrets.json
    dast.json              # phase 5
    fuzz/                  # phase 5
    mutation.json          # phase 2
  report.md
  report.json
  logs/
    planner.log
    gen_functional.log
    gen_sast.log
    executor.log
    evaluator.log
    triager.log
  memory/
    session_insights.json
```

### Repository layout post-fork

```
PFactory/                         # cp -r AIFactory, then surgically edit
  apps/
    backend/
      agents/
        planner.py                # rewrite for test planning
        gen_functional.py         # NEW
        gen_sast.py               # NEW
        gen_dast.py               # NEW (phase 5)
        gen_mutation.py           # NEW (phase 2)
        evaluator.py              # NEW — structurally separate
        triager.py                # NEW
        # delete: coder.py
      prompts/
        planner.md                # rewrite
        gen_functional.md         # NEW
        gen_sast.md               # NEW
        gen_dast.md               # NEW (phase 5)
        gen_mutation.md           # NEW (phase 2)
        evaluator.md              # NEW
        triager.md                # NEW
        # delete: coder.md, qa_*.md (logic absorbed into evaluator)
      qa/                         # delete entirely — replaced by evaluator
      mcp_server/
        pfactory_server.py        # rename, rewire tool surface
      tools/
        executor.py               # adapt: add docker-runner shim
        runners/
          docker_runner.py        # NEW
          lang_registry.py        # NEW per-lang tool tables
        # delete: github PR-merge workflow
      context/
        project_analyzer.py       # reuse as-is
      providers/                  # reuse as-is
      implementation_plan/        # keep models; rename to test_plan/
      memory/                     # reuse as-is
      spec_runner.py              # delete — PFactory consumes, doesn't create
      spec_agents/                # delete
    web-server/                   # reuse FastAPI; trim spec-creation routes
    frontend-web/                 # retheme: drop wizard, add findings/lanes
  .claude/skills/
    handover-to-pfactory/         # NEW — mirrors AIFactory's handover SKILL.md
    # delete: aifactory-spec (PFactory has no spec creation)
  .mcp.json                       # point at start-pfactory-mcp.sh
  scripts/
    start-pfactory-mcp.sh
    start-pfactory-portal.sh
  docker/
    runners/
      python.Dockerfile           # NEW
      node.Dockerfile             # NEW (phase 4)
  CLAUDE.md                       # update for PFactory-specific guidance
```

### Tooling table

| Lane | Python | TypeScript (phase 4) |
|---|---|---|
| Functional | `pytest` (+ unittest) | `vitest` (preferred) / `jest` |
| SAST | Semgrep + Bandit | Semgrep + eslint-plugin-security |
| Dep CVE | `pip-audit` + OSV | `npm audit` + OSV |
| Secrets | `gitleaks` / `trufflehog` | same |
| DAST (phase 5) | OWASP ZAP automation | OWASP ZAP automation |
| Fuzzing (phase 5) | `atheris` | `jsfuzz` / `fast-check` |
| Mutation (phase 2) | `mutmut` (default) / `cosmic-ray` | `stryker` |
| Property (phase 2+) | `Hypothesis` | `fast-check` |

Per-language tool selection lives in `apps/backend/tools/runners/lang_registry.py`
so generator prompts can ask "what's the mutation tool for this project?" via MCP tool.

---

## MVP scope (Phase 1)

### What ships

1. `cp -r AIFactory PFactory` and rename/strip down per layout above.
2. `/handover-to-pfactory` Claude Code skill that calls
   `mcp__pfactory__task_create_and_run` with
   `{project_id, spec_id, branch, base_ref}`.
3. PFactory MCP server (`apps/backend/mcp_server/pfactory_server.py`) exposing:
   - `task_create_and_run`
   - `task_status`
   - `task_list`
   - `project_list`, `project_create`
   - `report_get` — markdown + json
   - `task_rerun` (single lane)
4. **Planner agent** that reads `aifactory_spec.md` + diff + acceptance criteria,
   emits `test_plan.json` with `functional` subtasks only at MVP.
5. **Gen-Functional agent** (Python only) — pytest test generator with:
   - Pre-flight static check: imports resolve, target methods exist (kill hallucinations).
   - Flake-risk lint (unordered collection assertions, timing assumptions).
   - Allow-listed API exposure: agent only sees the diff'd code + its direct deps' public API, not the wider universe.
6. **Executor** with Docker runner shim. One base image at MVP:
   `pfactory-runner-python` (Debian slim + Python 3.12 + pytest + coverage).
   Repo bind-mounted read-only, scratch volume writable, network off.
7. **Evaluator** (structurally separate agent) — coverage delta, flake-lint
   score, basic LLM judgment of "are these tests semantically relevant".
   Mutation scoring deferred to Phase 2 — flag this as a known gap in the
   MVP report ("trivial-test risk; mutation gate ships in Phase 2").
8. **Triager** — dedup, rank, render `report.md` + `report.json`.
9. **Git side-effects** — `git commit` tests under `tests/` on the AIFactory
   feature branch; `gh pr comment <pr>` with the report body.
10. **Portal** — retheme inherited React app on port `:3102`:
    - `/tasks` (list, status)
    - `/tasks/<id>` (live logs, single-lane tabs — only `functional` lit at MVP)
    - `/projects`
    - skip `/findings` cross-task view at MVP

### What's explicitly NOT in MVP

- SAST, deps, secrets, DAST, fuzz, mutation lanes (all phase 2+)
- TypeScript target (phase 4)
- Other languages (phase 6+)
- Spec creation in PFactory (never — PFactory consumes specs)
- Cross-task findings triage view in portal
- Audit log / cost reporting (phase later — note as gap)
- E2B / Firecracker isolation (Docker is enough at MVP)

---

## Phase roadmap

| Phase | Adds | Why this order |
|---|---|---|
| **1 (MVP)** | Pipeline end-to-end; functional lane; Python; auto-commit + PR comment; retheme portal | Proves the architecture |
| **2** | Mutation lane (`mutmut`); evaluator gains mutation-score gating; tests with 0 kills are flagged or auto-rejected | Closes the trivial-test loophole — non-negotiable for trust |
| **3** | SAST (Semgrep + Bandit) + deps (`pip-audit` + OSV) + secrets (`gitleaks`); findings table in portal; cross-task triage view | Easy, high-value, no code-execution risk |
| **4** | TypeScript across all lit lanes — `vitest`, Semgrep + `eslint-plugin-security`, `npm audit`, `stryker` | Dogfoods AIFactory frontend; same pipeline, new generator prompts + Docker image |
| **5** | DAST (OWASP ZAP) + fuzzing (`atheris`, `jsfuzz`); network policy maturation; optional E2B/Firecracker for fuzz isolation | Harder infra, biggest blast-radius risk — last for a reason |
| **6** | Language expansion: Go, Rust, Ruby. Per-language Docker images + tool registry entries | Linear, low-architecture-risk work |

---

## Critical files to create or modify

### New files (MVP)

- `PFactory/.claude/skills/handover-to-pfactory/SKILL.md` — mirror of AIFactory's `handover/SKILL.md` (160 lines) with payload schema swapped.
- `PFactory/apps/backend/mcp_server/pfactory_server.py` — rename of AIFactory's `aifactory_server.py`; tool list swapped per MVP spec.
- `PFactory/apps/backend/agents/planner.py` — net new (AIFactory's planner is for code, not tests).
- `PFactory/apps/backend/agents/gen_functional.py` — net new.
- `PFactory/apps/backend/agents/evaluator.py` — net new.
- `PFactory/apps/backend/agents/triager.py` — net new.
- `PFactory/apps/backend/prompts/planner.md` — net new (test-oriented).
- `PFactory/apps/backend/prompts/gen_functional.md` — net new.
- `PFactory/apps/backend/prompts/evaluator.md` — net new.
- `PFactory/apps/backend/prompts/triager.md` — net new.
- `PFactory/apps/backend/tools/runners/docker_runner.py` — net new.
- `PFactory/apps/backend/tools/runners/lang_registry.py` — net new.
- `PFactory/docker/runners/python.Dockerfile` — net new.
- `PFactory/scripts/start-pfactory-mcp.sh` — adapt from AIFactory's `start-aifactory-mcp.sh`.

### Files to delete from the fork

- `PFactory/apps/backend/agents/coder.py` and `coder_*` siblings
- `PFactory/apps/backend/qa/` (logic absorbed into evaluator)
- `PFactory/apps/backend/spec_runner.py`
- `PFactory/apps/backend/spec_agents/`
- `PFactory/apps/backend/prompts/coder*.md`, `qa_*.md`, `complexity_assessor.md`, `followup_planner.md`
- `PFactory/.claude/skills/aifactory-spec/`
- `PFactory/.claude/skills/handover/` (replaced by `handover-to-pfactory/`)
- `PFactory/apps/backend/runners/github/` PR-merge workflow (replaced by simpler `gh pr comment` + commit logic in triager)
- `PFactory/apps/frontend-web/` spec creation wizard + plan approval components

### Files to reuse as-is

- `PFactory/apps/backend/context/project_analyzer.py`
- `PFactory/apps/backend/providers/` (whole package)
- `PFactory/apps/backend/agents/memory_manager.py`
- `PFactory/apps/backend/tools/executor.py` (with the docker runner added alongside, not replacing)
- `PFactory/apps/backend/implementation_plan/models.py` (rename module to `test_plan/` but keep model shape)

### AIFactory side (small change)

- Add new skill `AIFactory/.claude/skills/handover-to-pfactory/` that calls
  `mcp__pfactory__task_create_and_run` after AIFactory's QA passes.
- Optionally, add `--also-test` flag to AIFactory's `/handover` to auto-chain.
- This is the only AIFactory-side change — PFactory stays the active party.

---

## Verification plan

End-to-end smoke that MVP works:

1. **Pick a real AIFactory spec.** Use any small Python feature AIFactory has already
   shipped (e.g., a new endpoint + handler) so we have a known-good spec dir.
2. **Run PFactory locally.** `scripts/start-pfactory-portal.sh` (FastAPI on `:3102`,
   React on `:3103` or however the fork lands). Confirm portal loads and tasks
   list is empty.
3. **Trigger handover.** In a Claude Code session, run
   `/handover-to-pfactory --spec <spec_id>`. Expect:
   - Task appears in `~/.pfactory/workspaces/<project_id>/specs/<new_id>/`.
   - `task.md`, `context/source.json`, snapshot of AIFactory spec, diff written.
   - Portal `:3102/tasks` shows it pending → planning → generating → running →
     evaluating → triaging → done.
4. **Check generated tests.** `cd <project> && git log --oneline` — should show a
   new commit `pfactory: tests for <spec_id>` with files under `tests/`.
5. **Run the generated tests against current code.** `pytest tests/`. Should pass.
6. **Mutate one line of the feature code, rerun.** At least one generated test
   should fail (proves tests have signal).
7. **Check PR comment.** `gh pr view <pr> --comments` — PFactory's report should
   be there, summarising coverage delta + (placeholder) mutation note + flake-lint
   warnings if any.
8. **Hallucination guard test.** Feed Planner a spec referring to a method that
   doesn't actually exist in the diffed code. Expect Gen-Functional to detect
   via pre-flight check and ask Planner to revise, not emit a hallucinated test.
9. **Failure-path test.** Kill the docker daemon mid-task. Expect graceful
   failure with a clear error in `report.md` and task status `failed`, not a
   hang.

---

## Risks and mitigations

| Risk | Mitigation in this design |
|---|---|
| LLM tests hallucinate imports / methods (~39% of failures) | Pre-flight static check in Gen-Functional: every `import` and every method call resolves against the project's actual symbols before commit. Reject + replan if not. |
| LLM tests are trivial (assert True, no mutation kills) | Mutation lane in Phase 2 is the gate. MVP explicitly notes this gap; evaluator can run a sanity-check "would these tests fail if I delete a random line of the feature code" as a cheap proxy. |
| LLM tests are flaky | Evaluator runs each new test 3x in MVP; static lint for unordered-collection + timing patterns. |
| Generated test code does something nasty (deletes files, exfiltrates) | Docker runner, network off by default, repo bind-mounted read-only at the runner. |
| AIFactory spec schema changes break PFactory | PFactory snapshots AIFactory's spec into its own `context/` at handover time — PFactory operates on the snapshot, not a live reference. Schema check at the read boundary. |
| Cost runaway on long specs | Per-task token budget cap enforced at Executor layer; planner instructed to scope to diff lines only. |
| AIFactory and PFactory infra modules drift | Acknowledged trade-off of hard fork. Mitigation deferred — could later extract a `factory-core` package once divergence is painful. |
| Portal becomes confusing with empty lane tabs at MVP | At MVP, hide lane tabs whose generator is not yet implemented; show them as "coming in Phase N" placeholders. |

---

## Recommended next step

With this design locked, the natural next move is to break it into an
implementation backlog. Three options:

- **A) `/superhuman`** — let the AI-native SDLC skill decompose the MVP scope
  into dependency-graphed sub-tasks and execute them in parallel waves. Best
  fit given the user already runs AIFactory; this is the AIFactory analog
  inside Claude Code.
- **B) `/create-spec`** — use the user's own Agent OS workflow to turn this
  plan into a structured `.agent-os/specs/2026-05-28-pfactory-mvp/` spec
  with tasks.md, then `/execute-tasks`. Most aligned with the user's stated
  preferences in `~/.agent-os/`.
- **C) Hand this plan to AIFactory itself.** Drop this plan into AIFactory
  via its own `/handover`, let it bootstrap PFactory by forking + gutting +
  scaffolding the new files. Most poetic; also stress-tests AIFactory on
  a non-trivial spec.

My recommendation: **B** — `/create-spec` produces a checkable task list that
fits the user's Agent OS conventions. **C** is more fun and is a credible
victory lap once the MVP shape is real.
