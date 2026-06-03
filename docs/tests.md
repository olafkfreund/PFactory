---
layout: default
title: Test Coverage Spec
permalink: /tests/
nav_order: 5
---

# Test Coverage Specification

> ⏳ **Historical record — v0.1.0-mvp test plan (2026-05-28).** Covers the v0.1
> Functional-lane MVP. The shipped product moved to the v0.2 modality spine
> (unit / browser / api / integration / mutation); see [Architecture](/architecture/).

> Spec: PFactory MVP — Walking Skeleton (Functional Lane, Python)
> Parent: `../spec.md`
> Approach: TDD — every code-bearing task writes tests first, then implementation, then verifies green.

## Test pyramid for PFactory itself

```
       ┌──────────────────┐
       │   e2e smoke (4)  │   end-to-end pipeline against known good input
       ├──────────────────┤
       │ integration (12) │   agent + executor + git wiring
       ├──────────────────┤
       │   unit (~40)     │   per-module, fast, mocked LLM calls
       └──────────────────┘
```

## Unit tests (per module)

### `mcp_server/pfactory_server.py`

- MCP server starts and lists the six declared tools without error.
- `task_create_and_run` with valid payload creates a workspace dir with correct layout and enqueues a task; returns `{task_id, portal_url, spec_dir}`.
- `task_create_and_run` with unknown `project_id` returns a structured MCP error, no side effects.
- `task_create_and_run` with `confirm=false` returns a preview without enqueuing.
- `task_status` returns expected schema; unknown `task_id` → error.
- `task_rerun` only allows `lane=functional` at MVP; other lanes return "lane not implemented in MVP".

### `test_plan/` model

- `TestPlan` JSON round-trips identically to the AIFactory `ImplementationPlan` shape.
- Each `Subtask` carries a `lane` field, validated against the enum `{functional, sast, dast, fuzz, mutation}`.
- Status transitions enforced: `pending → in_progress → {completed | failed | stuck}` only.
- Backward serialization with AIFactory's `implementation_plan.json` is NOT required (PFactory is a hard fork).

### `context/source.json` snapshot

- Snapshot copy of AIFactory spec dir is read-only (file mode 0o444 after copy).
- `source.json` records `{aifactory_spec_dir, branch, base_ref, sha_at_handover, snapshotted_at}` with valid ISO timestamps.
- If AIFactory's spec dir is missing, snapshot raises a clear error and the task is marked `failed` before any agent runs.

### Planner agent

- Given a fixed `aifactory_spec.md` + `diff.patch` fixture, planner emits `test_plan.json` with at least one `functional` subtask referencing the diffed function.
- LLM is mocked at this layer; the test fixes the model's return and asserts the parser builds the right model.
- On replan request (rejection input present), planner emits a *different* subtask for the same target (no duplicate).
- 2 replans then `status: stuck` for that subtask.

### Gen-Functional agent — pre-flight static check

- Test "hallucinated import": generated test imports `from foo import bar` where `foo.bar` does not exist; pre-flight rejects.
- Test "hallucinated method": generated test calls `target.nonexistent_method()`; pre-flight rejects.
- Test "valid imports + methods": passes, gets written to `tests/functional/`.
- The introspection uses a subprocess in a venv mirroring the project's `pyproject.toml` (test verifies subprocess invocation and dependency installation step).

### Gen-Functional agent — flake-risk lint

- Each known pattern has a positive (flake-prone) and negative (clean) test case:
  - Dict iteration order assertion → high severity → reject.
  - `time.sleep` in test → medium severity → flag.
  - `datetime.now()` without freezing → medium → flag.
  - Set iteration order → high → reject.
  - Random ordering without seed → high → reject.
- Clean test passes all patterns.

### Docker runner

- `docker run` command is constructed with `--network=none`, `--read-only`, repo mounted ro, scratch mounted rw, CPU/memory/PID limits.
- Coverage XML and JUnit XML round-trip out via `/scratch/`.
- Timeout enforces hard kill (test with a script that sleeps longer than timeout; verify container is force-stopped and task marked `executor_timeout`).
- Image build is idempotent (`docker build` second run is a no-op).
- Podman-rootless invocation also tested (skipped if podman absent).

### Evaluator

- **Coverage delta**: with a fixture before/after coverage XML, returns expected `coverage_delta_pct`.
- **3x stability**: a test that always passes returns `stable`; a test that fails 1/3 returns `flaky`.
- **Mutate-and-check probe**: with a fixed seed for mutation point selection, a test that exercises the mutated line returns `killed=true`; a trivial test returns `killed=false`.
- **LLM semantic relevance**: LLM mocked; verifies the verdict pipeline routes `reject` / `flag` / `accept` correctly.

### Triager

- Dedup: two byte-identical test files produce one accepted file.
- Dedup: two files differing only in whitespace/comments produce one accepted file (normalization applied).
- Rank: tests sorted by coverage_delta desc with flagged at bottom.
- Report rendering: golden-file snapshot test for `report.md` (deterministic given fixed inputs).

### Git writer

- Constructs commit message with the right prefix and Co-Authored-By line.
- Detects existing tests dir; falls back to creating `tests/` if absent.
- `gh pr comment` invocation builds the right argv (dry-run mode for the unit; real invocation in integration).

## Integration tests (multi-component, no real LLM)

1. **Handover end-to-end (mocked LLM)** — `task_create_and_run` → workspace created → snapshot AIFactory dir → planner runs with mocked LLM returning a fixed plan → Gen-Functional runs with mocked LLM returning known test source → Executor runs in real Docker against a fixture project → Evaluator scores → Triager writes report. No network. No real AIFactory required (use a checked-in fixture spec dir).

2. **Hallucination replan loop** — Planner returns a subtask for a method that doesn't exist; Gen-Functional rejects in pre-flight; Planner is re-invoked; Gen-Functional accepts the replanned subtask. Verify counts in `report.json`.

3. **Flake-lint pipeline** — Gen-Functional emits a test with a dict-order assertion; flake-lint rejects; report counts `flake_warnings`. Then a clean test passes through.

4. **Stability re-run** — Generated test that fails 1/3 times is flagged not rejected; reported as `flaky`.

5. **Docker timeout** — generated test sleeps forever; executor kills container after `PFACTORY_TASK_TIMEOUT_SEC`; task marked `executor_timeout`; report describes the failure clearly.

6. **Docker daemon down** — kill the docker daemon before task; verify graceful failure with a clear error in `report.md` and status `failed`, no hang. (Maps to verification scenario #9 in the design plan.)

7. **Git commit + PR comment (dry-run)** — Triager produces git commands and a PR comment body that match goldens. Real git commit verified separately in e2e.

8. **Portal task list** — backend returns task list JSON shape; frontend renders task list (component test).

9. **Portal live logs WebSocket** — connection established; messages streamed; client renders.

10. **Portal lane tabs** — functional tab fully populated; sast/dast/fuzz/mutation tabs show "coming in Phase N" placeholder.

11. **MCP `task_rerun`** — completed task rerun rebuilds tests but reuses the snapshotted context; preserves logs of the previous run under `logs/run_1/`, `logs/run_2/`.

12. **AIFactory spec snapshot is read-only** — verify the original AIFactory spec dir is unchanged byte-for-byte after a PFactory run.

## End-to-end smoke (real LLM, real Docker, real git)

These run the 9 verification scenarios from the design plan against a known small Python feature spec from AIFactory's actual history. Documented as a CI workflow + a `scripts/e2e-smoke.sh` script.

1. **Happy path** — verification scenario 1-5 from design plan: handover → workspace populated → portal advances → tests committed → `pytest tests/` passes.
2. **Mutation-of-feature test** — verification scenario 6: mutate one line of feature code, rerun the generated tests, at least one must fail.
3. **PR comment** — verification scenario 7: `gh pr view --comments` shows PFactory's report.
4. **Hallucination guard** — verification scenario 8: planner fed a spec referring to a non-existent method; Gen-Functional rejects via pre-flight; Planner replans; no broken test committed.
5. **Docker-down failure path** — verification scenario 9: docker daemon killed mid-task; task marked `failed` with clear error in `report.md`; no hang.

## Mocking strategy

- **LLM mocking**: Claude Agent SDK calls are mocked at the SDK boundary, returning JSON fixtures from `tests/fixtures/llm/<scenario>/`. No real LLM calls in unit or integration tests. Real LLM only in e2e smoke and only when an env var is set (`PFACTORY_E2E_LLM=1`).
- **Docker mocking**: unit tests stub the Docker SDK calls (no real container); integration tests use a tiny `pfactory-runner-python` image built once in CI.
- **Git mocking**: unit tests dry-run; integration tests use a temp local clone fixture with no remote; e2e uses a sandbox GitHub repo (`pfactory-sandbox` configured for the test).
- **GitHub CLI**: `gh pr comment` mocked via env override (`GH_PATH=/tmp/fake-gh.sh`) in integration; real `gh` in e2e against the sandbox repo.

## Coverage targets

- Unit coverage ≥ 85% on new MVP modules (planner, gen_functional, evaluator, triager, docker_runner, git_writer, mcp_server/pfactory_server).
- Integration tests must pass in ≤ 5 minutes total on a single workstation.
- E2E smoke can take longer (up to 20 minutes); runs nightly in CI.

## What is intentionally not tested at MVP

- Mutation testing pipeline (no implementation to test).
- SAST / deps / secrets / DAST / fuzz lanes.
- TypeScript runner image.
- E2B / Firecracker isolation.
- Cross-task triage UI.
- Audit log / cost reporting.
