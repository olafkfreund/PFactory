---
layout: default
title: Technical Spec
permalink: /technical-spec/
nav_order: 4
---

# Technical Specification

> ⏳ **Historical record — v0.1.0-mvp technical spec (2026-05-28).** Describes
> the v0.1 Functional-lane MVP architecture. The shipped product moved to the
> v0.2 modality spine (unit / browser / api / integration / mutation); see
> [Architecture](/architecture/) for current state.

> Spec: PFactory MVP — Walking Skeleton (Functional Lane, Python)
> Parent: `../spec.md`
> Source design: `/home/olafkfreund/.claude/plans/virtual-cooking-bumblebee.md`

## Architecture summary

Six-agent pipeline mirroring the canonical Planner → Generator → Executor → Evaluator → Triager pattern. The Evaluator is structurally separate from the Generator (research-mandated for non-self-validation). At MVP only the `functional` lane is lit; the SAST / DAST / fuzz / mutation generators are not implemented, but the lane-tagged plan and dispatcher already account for them so phase 2-5 work is additive.

```
AIFactory finished branch
   |
   v
/handover-to-pfactory (Claude Code skill)
   -> mcp__pfactory__task_create_and_run
       {project_id, spec_id, branch, base_ref}
   |
   v
PFactory MCP server (stdio)
   -> POST /tasks (FastAPI backend on :3102)
   |
   v
Worker pulls task, creates ~/.pfactory/workspaces/.../{new spec_id}/
   snapshots AIFactory spec dir read-only into context/
   computes diff (base_ref..branch) into context/diff.patch
   runs project_analyzer -> context/project_analysis.json
   |
   v
Planner agent (Claude Agent SDK)
   -> emits test_plan.json (functional subtasks only at MVP)
   |
   v
Per-lane Generator (only Gen-Functional lit at MVP)
   -> generates pytest files into tests/functional/
   -> pre-flight static check (imports + methods resolve)
   -> flake-risk lint
   -> retries via planner replan on hallucination
   |
   v
Executor (shared)
   -> docker run --rm --network=none \
        -v <repo>:/work:ro \
        -v <scratch>:/scratch:rw \
        pfactory-runner-python \
        pytest --cov=<target_pkg> /scratch/tests
   -> collects junit.xml + coverage.xml + stdout/stderr
   |
   v
Evaluator (separate agent)
   -> coverage delta vs base_ref
   -> flake-lint score
   -> 3x stability re-run
   -> LLM semantic relevance judgment (per test)
   -> mutate-and-check sanity probe
   -> per-test verdict { accept | reject | flag } + rationale
   |
   v
Triager
   -> dedup, rank
   -> render report.md + report.json
   -> git commit accepted tests on AIFactory feature branch
   -> gh pr comment <pr> --body REPORT
```

## Inputs / outputs (contracts)

### Inbound: `mcp__pfactory__task_create_and_run`

```json
{
  "project_id": "string",
  "spec_id": "string",
  "branch": "string",
  "base_ref": "string",
  "confirm": true
}
```

- PFactory resolves `aifactory_spec_dir = ~/.aifactory/workspaces/{project_id}/specs/{spec_id}/` and reads it read-only.
- Returns `{ task_id, portal_url, spec_dir }` immediately; pipeline runs asynchronously.

### Outbound: report (markdown rendered, JSON stored)

```json
{
  "task_id": "string",
  "spec_id": "string",
  "lane_results": {
    "functional": {
      "tests_generated": 17,
      "tests_accepted": 14,
      "tests_rejected": 2,
      "tests_flagged": 1,
      "coverage_delta_pct": 6.3,
      "flake_warnings": ["tests/functional/test_oauth.py::test_lookup uses dict iteration order"],
      "hallucination_replans": 1,
      "mutate_probe_killed": true
    }
  },
  "git": {
    "commit_sha": "abc1234",
    "files_added": ["tests/functional/test_login.py", "..."],
    "pr_comment_url": "https://github.com/..."
  },
  "phase2_gap_notice": "Mutation gating not yet active; trivial-test risk remains until phase 2."
}
```

## Component-level technical detail

### Hard-fork scaffold

- Operation: `cp -r AIFactory/. PFactory/`, then surgical deletions and renames per the file lists in the design plan ("Critical files to create or modify" and "Files to delete from the fork").
- All `aifactory` string references renamed to `pfactory` in: module names (`mcp_server/aifactory_server.py` → `mcp_server/pfactory_server.py`), env vars, `.mcp.json`, scripts in `scripts/`, port defaults, prompt directives. Use `rg -l aifactory PFactory/` to enumerate before renaming; verify zero matches post-rename except in `context/source.json` references (which intentionally point back to AIFactory) and explicit documentation about the fork relationship.
- The `.agent-os/` directory (this spec) survives the fork untouched.
- Python imports verified post-fork: `python -m apps.backend.mcp_server.pfactory_server --help` must run without import errors before any further work begins.

### MCP server (`apps/backend/mcp_server/pfactory_server.py`)

MVP tool surface (subset of AIFactory's):

- `task_create_and_run(project_id, spec_id, branch, base_ref, confirm)` — main entry.
- `task_status(task_id)` — returns `{ status, current_phase, lane_progress, started_at, updated_at }`.
- `task_list(project_id?, limit?, status?)`.
- `project_list()`, `project_create(name, root_path)`.
- `report_get(task_id, format='md'|'json')`.
- `task_rerun(task_id, lane='functional')` — reruns one lane against the same context snapshot.

`.mcp.json` updated to point at `scripts/start-pfactory-mcp.sh` which invokes `python -m apps.backend.mcp_server.pfactory_server`.

### Handover skill

`PFactory/.claude/skills/handover-to-pfactory/SKILL.md`:

- YAML frontmatter mirrors AIFactory's `handover/SKILL.md` shape.
- `allowed-tools` list contains the six PFactory MCP tools above.
- Procedure mirrors AIFactory's: infer args from chat → validate via `project_list` → call `task_create_and_run` → report `task_id` and portal URL.
- Trigger phrases include `/handover-to-pfactory` and natural language like "hand this off to pfactory to generate tests".

Companion skill on AIFactory side: `AIFactory/.claude/skills/handover-to-pfactory/SKILL.md` is the *user-facing* skill that lives in AIFactory's repo (since the slash command is typed while working in an AIFactory-tracked project). It MCP-calls PFactory's server. This is the only AIFactory-side change in MVP.

### Workspace + state model

- Root: `~/.pfactory/workspaces/{project_id}/specs/{spec_id}/`
- Generated structure as specified in spec.md scope item 3 and in the design plan's "Spec dir layout (per task)" section.
- `test_plan.json` shape mirrors AIFactory's `implementation_plan.json` (Phase containing Subtasks with status enum) but each Subtask carries a `lane` field ∈ `{functional, sast, dast, fuzz, mutation}`. At MVP only `functional` subtasks are emitted.
- The module is renamed: `apps/backend/implementation_plan/` → `apps/backend/test_plan/`. The model classes inside keep the same shape; only the namespace changes.
- `context/source.json` is the cross-reference record `{ aifactory_spec_dir, branch, base_ref, sha_at_handover, snapshotted_at }`. PFactory reads AIFactory's spec dir once at handover time and copies the relevant files into `context/`; subsequent work uses the snapshot, never the live AIFactory dir.

### Planner agent

- File: `apps/backend/agents/planner.py`
- Prompt: `apps/backend/prompts/planner.md` (net new, not adapted from AIFactory's planner.md which is code-generation oriented).
- Inputs: `context/aifactory_spec.md` (acceptance criteria), `context/diff.patch`, `context/project_analysis.json`.
- Output: `test_plan.json` with phases / subtasks. Each subtask has `lane: functional`, a `target` (file path + symbol), and a `rationale` referencing the acceptance criterion it covers.
- Replan path: when Gen-Functional rejects a subtask (e.g. hallucinated method), Planner is re-invoked with the rejection reason to emit a corrected subtask. Limit: 2 replans per subtask, then `status: stuck` and the test is omitted from the commit.

### Gen-Functional agent

- File: `apps/backend/agents/gen_functional.py`
- Prompt: `apps/backend/prompts/gen_functional.md`
- Allow-listed context exposure: agent sees diffed code + each diffed module's `import`-resolved direct dependencies' public API (signatures only, not full source). No wider repo access.
- Pre-flight static check (mandatory before write):
  - Parse generated test with `ast`.
  - Every `import` resolves to a real module reachable from the project's `sys.path` at runtime.
  - Every attribute access on imported symbols resolves to an actual attribute (introspect via importing into a subprocess in a venv mirroring the project's `pyproject.toml`).
  - If any check fails, the test is rejected and Planner is asked to replan that subtask.
- Flake-risk lint (mandatory before write):
  - Pattern check: assertions on dict iteration order, set iteration order, unordered comparisons, `time.sleep` in tests, dependence on `datetime.now()` without freezing.
  - Each pattern carries a severity; high-severity patterns reject; medium-severity flag.
- Output: pytest file(s) written to `tests/functional/` in the workspace (pre-commit; Triager handles commit).

### Docker executor (`apps/backend/tools/runners/docker_runner.py`)

- Per-task invocation:

  ```
  docker run --rm \
      --network=none \
      --read-only \
      -v <repo_path>:/work:ro \
      -v <scratch_path>:/scratch:rw \
      --cpus=2 --memory=2g --pids-limit=512 \
      pfactory-runner-python \
      bash -c "cd /work && cp -r /scratch/tests . && pytest --cov=<pkg> --cov-report=xml --junitxml=/scratch/junit.xml tests/functional"
  ```

- Reads JUnit XML + coverage XML back from `/scratch/` for the Evaluator.
- Timeout: 10 minutes default; configurable per-task. Hard kill on timeout, marked as `executor_timeout` failure.
- The native pass-through path (for the eventual SAST lane) is a function in the same module that runs commands in-process with a captured working directory and a read-only filesystem walker. Interface is stubbed at MVP, not used.

### Dockerfile (`docker/runners/python.Dockerfile`)

- Base: `python:3.12-slim-bookworm`.
- Installs: `pytest`, `pytest-cov`, `pip-audit` (deferred but pre-installed so phase 3 doesn't rebuild), `coverage`.
- USER: non-root (`uid=1000`).
- Entrypoint accepts arbitrary command; default is bash.

### Evaluator agent

- File: `apps/backend/agents/evaluator.py`
- Prompt: `apps/backend/prompts/evaluator.md`
- Inputs: per-test source, per-test JUnit + coverage data, the diffed feature code.
- Checks:
  1. **Coverage delta** — lines covered by this test that weren't covered by base.
  2. **Flake-lint score** — from Gen-Functional's earlier scan; promoted to verdict here.
  3. **3x stability re-run** — Executor re-runs the test 3 times; flakes → flag.
  4. **LLM semantic relevance** — Claude reviews the test source vs the feature code, judges "does this test actually exercise the feature's behavior or is it tautological?"
  5. **Mutate-and-check sanity probe** — for each test, delete a random expression in the targeted feature function, rerun the test in Docker, expect it to fail. If it still passes, the test is trivial. (This is a cheap proxy for mutation testing until the real mutation lane lands in phase 2.)
- Verdict per test: `accept` / `reject` / `flag`. Only `accept` proceeds to commit. `flag` proceeds to commit but is annotated in the report.

### Triager + git side-effects

- File: `apps/backend/agents/triager.py` (plus a thin `apps/backend/tools/git_writer.py` shim).
- Dedup: hash test source post-normalization (whitespace, comments stripped); collapse duplicates.
- Rank: by coverage-delta desc, with flagged tests at the bottom.
- Report: render `report.md` (human-readable summary + per-test table) and `report.json` (machine).
- Git commits land on the AIFactory feature branch in the actual project repo at `context/project_root` (resolved at handover via `project_list` lookup). Test files written under the project's existing tests dir (detected by `project_analyzer`) or `tests/` if absent. Commit message: `pfactory: tests for <spec_id>\n\n<short summary>` + Co-Authored-By line.
- `gh pr comment <pr_number> --body $REPORT` via the existing GitHub runner module — heavily simplified from AIFactory's PR-merge workflow (delete most of `runners/github/`, keep only the comment helper).
- The portal also surfaces the report at `/tasks/<id>` from `report.md`.

### Portal retheme

- Backend: `apps/web-server/main.py` — trim spec-creation routes; new task endpoints `/tasks`, `/tasks/<id>`, `/tasks/<id>/logs/stream` (WebSocket), `/tasks/<id>/report.{md,json}`.
- Frontend: `apps/frontend-web/`
  - Drop: spec wizard, plan approval UI, follow-up planner UI.
  - Reuse: layout shell, auth, task list table, WebSocket live-update infra, log viewer component.
  - Add: lane status grid (5 cells: functional / sast / dast / fuzz / mutation — only functional active at MVP), per-lane tab in single-task view, report viewer (markdown), "coming in Phase N" placeholders for un-lit lanes.
- Port: `:3102` (backend), `:3103` (Vite dev server) to avoid clashing with AIFactory's `:3101` / equivalent.

## Configuration & environment

- New env vars: `PFACTORY_WORKSPACE_ROOT` (default `~/.pfactory/workspaces`), `PFACTORY_DOCKER_IMAGE_PYTHON` (default `pfactory-runner-python:latest`), `PFACTORY_PORTAL_PORT` (default `3102`), `PFACTORY_TASK_TIMEOUT_SEC` (default `600`).
- Reuse from AIFactory: model provider env (`ANTHROPIC_API_KEY`, etc.), Graphiti config, OAuth config (for portal auth).
- `.env.example` updated to reflect PFactory variables; AIFactory's `.env` is NOT shared at runtime — PFactory has its own.

## Dependencies and provenance

- All Python dependencies inherited from AIFactory's `pyproject.toml` + adds: `docker` (Python SDK) for runner orchestration.
- All JS dependencies inherited from AIFactory's frontend `package.json`; removals possible after wizard component deletion (defer to housekeeping later).
- Docker daemon required on host. `podman` rootless verified to work in the same calling shape; document as supported alternative.

## Out-of-scope reminders (for clarity)

- No mutation testing infra at MVP (real mutation lane = phase 2). The Evaluator's mutate-and-check probe is intentionally a cheap proxy, not full mutation testing.
- No SAST, no deps scan, no secrets scan, no DAST, no fuzzing at MVP (phases 3-5).
- No TypeScript runner image at MVP (phase 4).
- No cross-task triage view in portal (phase 3+).

## Open implementation questions to resolve during build

1. Container runtime: docker vs podman. NixOS-rootless podman is the user's natural choice; verify both work and document. Default `docker` since the Python `docker` SDK is the most universal.
2. Test-dir detection heuristic for projects without a `tests/` directory — fall back to creating one? Or refuse and report? Recommendation: create `tests/` and note in report.
3. Whether to commit `tests/_pfactory/REPORT.md` to the branch alongside source tests. Recommendation: yes, commit it — durability + grep-ability outweighs the noise.
4. How to handle multi-package monorepos at MVP. Recommendation: scope detection to the diff's directory subtree; reject (with clear error) if diff spans multiple top-level packages.
