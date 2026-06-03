# PFactory v0.2 — Implementation Task Plan

> **Status:** Tasks ready for execution
> **Date:** 2026-05-28
> **Parent design:** [`2026-05-28-enterprise-test-frameworks-design.md`](./2026-05-28-enterprise-test-frameworks-design.md)
> **Authored via:** `/super-brainstorm` → spec → writing-plans
> **Predecessor release:** v0.1.0-mvp (12 tasks, 531 backend + 112 frontend tests)

## Summary

**16 tasks**, ~95 commits, multi-month effort. Ships v0.2:
**Playwright + Jest + pytest** across three lanes (**Browser, Unit-TS, Unit-Python**).
Establishes the framework registry, target schema, and platform deliverables
that v0.3+ extensions slot into without rework.

Same execution cadence as v0.1: one GitHub issue per task, six-commit shape
per task (scaffold → primitives × 2 → real wire → integration → close).
Issue numbers below are tentative — will be assigned at issue-creation time.

---

## Dependency graph

```
              Task 0 (lane rename — gates everything)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
          Task 1          Task 2          Task 3
          framework       .pfactory.yml   tests-catalog
          registry        schema          schema
              │               │               │
              └───────────────┼───────────────┘
                              ▼
                          Task 4
                          snapshotter extended
                              │
                              ▼
                          Task 5
                          Planner per-subtask
                              │
                              ▼
                          Task 6
                          Gen-Functional generic + context
                              │
              ┌───────────────┼────────────────────────┐
              ▼               ▼                        ▼
          Task 7          Task 9                   Task 12
          Docker images   Evaluator                Templates
          (playwright,    per-lang primitives      (Playwright +
           jest, pytest)  (tsc, ESLint, Stryker)   Jest + pytest set)
              │               │                        │
              ▼               ▼                        ▼
          Task 8          Task 10                  Task 13
          Browser app     Evaluator coverage       Skills
          runtime         adapter (null vs zero)   (init, add-test,
                              │                    from-template)
                              ▼                        │
                          Task 11                      ▼
                          Triager update-vs-create  Task 14
                          + catalog mutation        Portal endpoints
                              │                        │
                              └────────────┬───────────┘
                                           ▼
                                       Task 15
                                       LaneStatusGrid +
                                       migration CLI
                                           │
                                           ▼
                                       Task 16
                                       Evidence capture +
                                       portal viewer
                                       (closes v0.2)
```

**Critical path:** `0 → 1/2/3 → 4 → 5 → 6 → 9 → 10 → 11 → 15 → 16`
**Parallelizable after Task 6:** 7, 8, 9, 12, 13, 14
**Task 16** lands LAST — it integrates evidence flow across Tasks 8 (browser
runtime emits artifacts), 11 (Triager links them), 14 (portal serves them).
Doing it last avoids re-touching three upstream tasks.

---

## Task index

| # | Title | Blocked by | Commits | Issue |
|---|---|---|---|---|
| 0 | Lane rename + breaking-change migration | — | 4 | tbd |
| 1 | Framework registry data model + loader | 0 | 6 | tbd |
| 2 | `.pfactory.yml` schema + parser + validator | 0 | 6 | tbd |
| 3 | `.pfactory/tests-catalog.json` schema + helpers | 0 | 6 | tbd |
| 4 | Snapshotter extended | 2, 3 | 4 | tbd |
| 5 | Planner per-subtask (language, framework, lane) | 1, 4 | 6 | tbd |
| 6 | Gen-Functional generic + context injection | 1, 5 | 6 | tbd |
| 7 | Per-framework Docker images (Playwright, Jest, pytest) | 1 | 5 | tbd |
| 8 | Browser-lane app runtime + health-poll | 2, 7 | 6 | tbd |
| 9 | Evaluator per-language primitives (tsc, ESLint, Stryker) | 1, 6 | 6 | tbd |
| 10 | Evaluator coverage adapter (null vs zero) | 6, 9 | 4 | tbd |
| 11 | Triager update-vs-create + catalog mutation | 3, 10 | 5 | tbd |
| 12 | Templates: Playwright + Jest + pytest starter set | 1 | 5 | tbd |
| 13 | Skills: pfactory-init / add-test / from-template | 12 | 5 | tbd |
| 14 | Portal endpoints for templates / skills / catalogs | 1, 3, 12, 13 | 6 | tbd |
| 15 | LaneStatusGrid reskin + migration CLI | 0, 14 | 5 | tbd |
| 16 | Test evidence capture + portal viewer (closes #v0.2) | 8, 11, 14 | 6 | tbd |

**Total:** ~95 commits across 16 tasks.

---

## Task 0 — Lane rename + breaking-change migration

> **Must land first.** Every other task depends on the new Lane enum.

### Goal

Atomically replace v0.1's `Lane.{FUNCTIONAL, SAST, DAST, FUZZ, MUTATION}`
with v0.2's `Lane.{UNIT, BROWSER, API, INTEGRATION, MUTATION}` across the
backend, frontend, tests, and prompts.

### Sub-tasks

- [ ] 0.1 Update `apps/backend/test_plan/lane.py`:
  - Rename `Lane.FUNCTIONAL` → `Lane.UNIT`
  - Add `Lane.BROWSER`, `Lane.API`, `Lane.INTEGRATION`
  - Remove `Lane.SAST`, `Lane.DAST`, `Lane.FUZZ` (with `__deprecated__` aliases that emit a warning + map to `Lane.UNIT` for legacy plan compatibility through v0.2; remove aliases in v0.3)
- [ ] 0.2 Update `apps/backend/tools/runners/lane_dispatch.py`:
  - `_MVP_LIT_LANES` frozenset → `{Lane.UNIT, Lane.BROWSER, Lane.API, Lane.INTEGRATION, Lane.MUTATION}`
  - `_LANE_PHASES` dict updated
- [ ] 0.3 Update `apps/backend/tools/runners/lang_registry.py`:
  - `_LANE_KEYS` tuple
- [ ] 0.4 Update `apps/backend/agents/tools_pkg/tools/task_control.py`:
  - `_MVP_LANES` tuple includes all 5 new lanes
- [ ] 0.5 Update tests with literal lane lists:
  - `tests/test_test_plan_lane.py` (currently asserts old set)
  - `tests/test_lane_dispatch.py` (currently asserts `"sast"` + `"phase 3"`)
  - `tests/test_lang_registry.py` (currently iterates old keys)
- [ ] 0.6 Update frontend:
  - `apps/frontend-web/src/components/pfactory/LaneStatusGrid.tsx` — TS union type
  - `apps/frontend-web/src/components/pfactory/__tests__/LaneStatusGrid.test.tsx:51` — array assertion
  - Lane card titles: Functional → Unit, SAST → Browser, DAST → API, Fuzz → Integration, Mutation stays
- [ ] 0.7 Add deprecation note to CHANGELOG.md under "v0.2 (in progress)"

### Acceptance criteria

- Backend non-SDK suite: 531 passed → still 531 passed (no regressions, just renames)
- Frontend suite: 112 passed → still 112 passed
- `verify-fork.sh`: PASS
- `grep -r "Lane.FUNCTIONAL\|Lane.SAST\|Lane.DAST\|Lane.FUZZ"` returns only the deprecation-alias lines

### Commit shape (4 commits)

1. Backend Lane enum + deprecation aliases + lane_dispatch + lang_registry
2. Backend tests updated
3. Frontend LaneStatusGrid + test updates
4. CHANGELOG + close issue

---

## Task 1 — Framework registry data model + loader

### Goal

Build the registry that maps framework name → `FrameworkDescriptor` object,
loaded from `frameworks/{name}/descriptor.yaml` files. Used by all
downstream agents to look up runner image, templates, context block,
evaluator hooks per (language, framework).

### Sub-tasks

- [ ] 1.1 `apps/backend/framework_registry/descriptor.py` — `FrameworkDescriptor` dataclass mirroring the YAML schema from the design doc
- [ ] 1.2 `apps/backend/framework_registry/loader.py` — `load_registry()` walks `frameworks/*/descriptor.yaml`, validates each, builds `dict[str, FrameworkDescriptor]`
- [ ] 1.3 `apps/backend/framework_registry/validator.py` — schema validation: required fields, version_range parsing, file-glob compilation, coverage_strategy enum
- [ ] 1.4 Three example descriptors: `frameworks/playwright/descriptor.yaml`, `frameworks/jest/descriptor.yaml`, `frameworks/pytest/descriptor.yaml`
- [ ] 1.5 `tests/test_framework_registry.py` — happy load, missing field rejection, version-range parsing, glob compilation, 3-descriptor end-to-end
- [ ] 1.6 Documentation: `docs/framework-registry.md` (how to author a descriptor)

### Acceptance criteria

- `from framework_registry import load_registry; r = load_registry(); assert "playwright" in r`
- All 3 descriptors validate
- Adding a 4th descriptor with `coverage_strategy: nonsense` rejects with a clear error
- 25+ tests pass

### Commit shape (6 commits)

1. Dataclass + validator scaffolding
2. Loader + first descriptor (pytest, mirrors v0.1 behavior as sanity)
3. Playwright descriptor + browser-specific fields
4. Jest descriptor
5. Tests + 25+ cases
6. Docs + close issue

---

## Task 2 — `.pfactory.yml` schema + parser + validator

### Goal

Define the schema that AIFactory projects use to declare targets, test
paths, and seed/reset commands. Implement parser + validator with helpful
error messages.

### Sub-tasks

- [ ] 2.1 `apps/backend/pfactory_yml/schema.py` — Pydantic models: `PFactoryConfig`, `Target`, `Auth`, `HealthCheck`, `WaitFor`, `TestData`
- [ ] 2.2 `apps/backend/pfactory_yml/parser.py` — `load_pfactory_yml(repo_root) -> PFactoryConfig | None`
- [ ] 2.3 Target type validators: `HttpTarget`, `KubernetesTarget`, `DockerComposeTarget` (each with type-specific required fields)
- [ ] 2.4 Auth validators: `bearer`, `basic`, `oauth2_client_credentials`, `serviceaccount`, `mtls`, `none`
- [ ] 2.5 Env-var indirection: detect `token_env:`, `client_secret_env:` etc.; resolution happens at Executor invocation, NOT at parse time (so the yaml can be shared without secrets)
- [ ] 2.6 `tests/test_pfactory_yml.py` — round-trip, each target type valid, missing required field rejection, env-var pattern validation
- [ ] 2.7 Example `.pfactory.yml.example` in repo root with comments

### Acceptance criteria

- A 4-target example yaml (http, kubernetes, docker-compose, feature_flag) parses cleanly
- Each target type's missing-field case rejects with a useful error
- The parser never resolves env vars (deferred to Executor)
- 30+ tests

### Commit shape (6 commits)

1. Pydantic models
2. Target type validators
3. Auth validators
4. Parser + env-var detection
5. Tests
6. Example yaml + close issue

---

## Task 3 — `.pfactory/tests-catalog.json` schema + helpers

### Goal

Define the catalog schema, read/write helpers, and the 3-step AC-match
lookup algorithm.

### Sub-tasks

- [ ] 3.1 `apps/backend/tests_catalog/schema.py` — `CatalogEntry` dataclass
- [ ] 3.2 `apps/backend/tests_catalog/io.py` — `load_catalog(repo_root)`, `save_catalog(repo_root, catalog)` with atomic-write pattern (write tmp, rename)
- [ ] 3.3 `apps/backend/tests_catalog/lookup.py` — `lookup_by_ac(catalog, candidate_ac)` implementing the 3-step algorithm from the spec
- [ ] 3.4 `apps/backend/tests_catalog/migration.py` — `migrate_v0_1_workspace(spec_dir, catalog)` walks v0.1 workspaces and populates catalog entries for previously-generated tests
- [ ] 3.5 `tests/test_tests_catalog.py` — round-trip, atomic-write race resistance, all 3 lookup paths (exact, prefix, no-match), migration end-to-end
- [ ] 3.6 Docs section in `docs/tests-catalog.md`

### Acceptance criteria

- `lookup_by_ac` returns exact matches first, prefix matches second, empty third (verified by 6+ test cases)
- Catalog round-trips byte-identical for the same input twice
- v0.1 migration: feed it the planner_smoke fixture's spec_dir + an empty catalog; emerges with N entries for the N generated test files
- 25+ tests

### Commit shape (6 commits)

1. Schema dataclass
2. Read/write IO with atomic-write
3. 3-step lookup algorithm
4. v0.1 migration
5. Tests
6. Docs + close issue

---

## Task 4 — Snapshotter extended

### Goal

The snapshotter (Task 3 of v0.1) currently captures AIFactory spec + diff
into `context/`. Extend to also read `.pfactory.yml` + `.pfactory/tests-catalog.json`
from the AIFactory repo and surface them to the Planner.

### Sub-tasks

- [ ] 4.1 `apps/backend/workspaces/snapshotter.py` — call `pfactory_yml.load_pfactory_yml(root_path)`; if present, copy into `spec_dir/context/pfactory_yml.json` (parsed form)
- [ ] 4.2 Call `tests_catalog.load_catalog(root_path)`; if present, copy into `spec_dir/context/tests_catalog.json`
- [ ] 4.3 Update `source.json` to record presence/absence of both
- [ ] 4.4 `tests/test_snapshotter.py` — add cases for projects with + without `.pfactory.yml`, with + without catalog

### Acceptance criteria

- Snapshotter handles all 4 combinations (yaml ✓/✗ × catalog ✓/✗) without error
- `source.json` correctly flags `has_pfactory_yml`, `has_tests_catalog`
- 8+ new test cases

### Commit shape (4 commits)

1. Read pfactory.yml in snapshotter
2. Read tests-catalog in snapshotter
3. source.json fields + tests
4. Close issue

---

## Task 5 — Planner per-subtask (language, framework, lane, target)

### Goal

Extend the Planner to emit subtasks each carrying `(language, framework,
lane, target_name)` instead of just `lane`. Polyglot repos produce mixed
subtasks naturally.

### Sub-tasks

- [ ] 5.1 Extend `test_plan/subtask.py` schema: `language: str`, `framework: str`, `target_name: str`
- [ ] 5.2 Update `prompts/planner.md` to read `pfactory_yml.json` + `tests_catalog.json` from `context/`
- [ ] 5.3 New planner prompt section: "Picking the framework" — instructs the LLM to apply the manifest+config sniff + LLM-fallback algorithm
- [ ] 5.4 Update `agents/planner.py`'s post-session validator: every subtask must declare valid (language, framework) per the framework registry
- [ ] 5.5 `prompts_pkg/prompts.py` — `get_pfactory_planner_prompt` now also injects the framework registry summary + tests-catalog summary
- [ ] 5.6 `tests/test_planner.py` — new cases: polyglot repo with mixed subtasks, catalog hit forces UPDATE intent, invalid (language, framework) rejected

### Acceptance criteria

- A polyglot fixture (Python backend + TS frontend) produces both pytest + Jest subtasks in one plan
- Subtask validator rejects `(language=java, framework=playwright)` (invalid combo)
- Catalog hit on an AC produces subtask with `intent: update` field
- 15+ new test cases

### Commit shape (6 commits)

1. Subtask schema extensions
2. Planner prompt updates (framework picking)
3. Post-session validator
4. Helper prompt injection
5. Tests (mocked SDK)
6. Close issue

---

## Task 6 — Gen-Functional refactored: generic prompt + context injection

### Goal

Replace `prompts/gen_functional.md` (currently Python+pytest-specific) with
a generic prompt that's parameterized per subtask via the framework
descriptor's `context_block` + `templates`.

### Sub-tasks

- [x] 6.1 Author new `prompts/gen-functional.md` (generic; language/framework variables substituted at runtime)
- [x] 6.2 `prompts_pkg/prompts.py` — `get_pfactory_gen_functional_prompt` accepts a `FrameworkDescriptor` + subtask, injects the descriptor's `context_block` + a chosen `template` (if applicable)
- [x] 6.3 Update `agents/gen_functional.py`: per subtask, look up framework via `framework_registry`, pass the descriptor to the prompt helper
- [x] 6.4 `agents/gen_functional.py`'s `_resolve_runner_fn` parameterized by framework's `runtime.image` (no more hardcoded `pfactory-runner-python`)
- [x] 6.5 `tests/test_gen_functional.py` — new cases: 3 frameworks each generate a valid test file (mocked SDK), context-block injection verified, template selection works
- [x] 6.6 Migration: keep old `gen_functional.md` as `prompts/gen_functional-v01-legacy.md` for one release with a deprecation banner

### Acceptance criteria

- Gen-Functional generates valid output for Playwright, Jest, and pytest subtasks (3 cases × mocked SDK)
- Each generated file follows the framework's `test_path_conventions`
- Context block injection asserted via prompt-content tests
- 20+ new test cases

### Commit shape (6 commits)

1. Generic prompt body
2. Prompt helper with descriptor injection
3. Agent dispatcher per framework
4. Runner-fn parameterization
5. Tests
6. Close issue

---

## Task 7 — Per-framework Docker images

### Goal

Build the runner images for Playwright + Jest. pytest's image already
exists from v0.1; rebuild to match the registry's `runtime.image` convention.

### Sub-tasks

- [ ] 7.1 `docker/pfactory-runner-pytest/Dockerfile` — rename from v0.1's existing image; ensure parity
- [ ] 7.2 `docker/pfactory-runner-jest/Dockerfile` — Node 22, Jest, nyc for coverage, ESLint pre-installed for the flake-lint analog
- [ ] 7.3 `docker/pfactory-runner-playwright/Dockerfile` — Node 22 + Playwright + browsers (chromium baseline; firefox + webkit as optional layers)
- [ ] 7.4 CI workflow: build all 3 images on push to main; tag with commit SHA + `latest`
- [ ] 7.5 `tests/test_docker_runner.py` — add cases asserting each image starts + runs a trivial smoke test (skip via env when docker daemon unavailable)

### Acceptance criteria

- All 3 images build under 5 minutes each
- Playwright image runs a hello-world spec against `data:text/html,<h1>hi</h1>`
- Jest image runs a hello-world test
- pytest image runs a hello-world test
- CI workflow green

### Commit shape (5 commits)

1. Rename pytest image to registry convention
2. Jest image
3. Playwright image (largest — chromium baseline)
4. CI workflow
5. Smoke tests + close issue

---

## Task 8 — Browser-lane app runtime + health-poll

### Goal

Make the Executor able to spin up the AIFactory app via docker-compose,
wait for it to be healthy, hand the URL to the Playwright test, then tear
it down.

### Sub-tasks

- [ ] 8.1 `apps/backend/tools/runners/app_runtime.py` — `AppRuntime` class wrapping docker-compose lifecycle
- [ ] 8.2 `start(target: DockerComposeTarget)` — `docker compose -f {file} up -d {services}` in a side-network
- [ ] 8.3 `wait_for_healthy(target)` — HTTP HEAD against each `wait_for.url` every 2s for up to 120s; fails with structured error including last response codes per URL
- [ ] 8.4 `stop(target)` — `docker compose down` with `--volumes` (clean state)
- [ ] 8.5 Integrate with `DockerRunner`: for Browser-lane tests, wrap the test invocation in an `AppRuntime` lifecycle
- [ ] 8.6 Status integration: `phase=app_not_healthy` if health-poll times out; `phase=executor_app_running` while alive
- [ ] 8.7 `tests/test_app_runtime.py` — mock docker-compose calls; test all 4 states (start ok, health ok, health timeout, stop)

### Acceptance criteria

- A Playwright subtask against the `planner_smoke` fixture's `docker-compose.yml` (new) starts the app, runs the spec, tears down
- Health-poll timeout produces `app_not_healthy` status with the last response code visible
- No orphan containers after task completion (verified by `docker ps -a | grep pfactory` returning empty)
- 12+ test cases

### Commit shape (6 commits)

1. AppRuntime class scaffolding
2. start + stop
3. wait_for_healthy with poll loop
4. DockerRunner integration
5. Status transitions
6. Tests + close issue

---

## Task 9 — Evaluator per-language primitives

### Goal

Author the TS/JS analogs of Python's `preflight_static`, `flake_risk_lint`,
`mutate_probe`. These are the per-language primitives the Evaluator
dispatches based on the test's framework.

### Sub-tasks

- [ ] 9.1 `apps/backend/agents/lang_typescript/preflight.py` — runs `tsc --noEmit {test_file}` in the runner image; parses output for unresolved imports
- [ ] 9.2 `apps/backend/agents/lang_typescript/flake_lint.py` — runs ESLint with a PFactory-tuned config banning `waitForTimeout`, hardcoded sleeps, `console.log` in tests, etc.
- [ ] 9.3 `apps/backend/agents/lang_typescript/mutate_probe.py` — runs Stryker with a config that does ONE mutation + run; parses Stryker's JSON report
- [ ] 9.4 ESLint config: `apps/backend/agents/lang_typescript/eslint-pfactory.config.json`
- [ ] 9.5 Stryker config template: `apps/backend/agents/lang_typescript/stryker.tmpl.json`
- [ ] 9.6 `tests/test_lang_typescript_*.py` — one test module per primitive, mocked subprocess + real TS source fixtures

### Acceptance criteria

- TS preflight detects unresolved imports (e.g. `from './does-not-exist'`)
- TS flake-lint flags `await page.waitForTimeout(1000)` as high severity
- TS mutate-probe correctly classifies a `assert.equal(x, 5)` as KILLED when mutated to `assert.equal(x, 6)`
- 35+ test cases (matches Python primitives' coverage)

### Commit shape (6 commits)

1. TS preflight (tsc-based)
2. TS flake-lint (ESLint-based)
3. TS mutate-probe (Stryker-based)
4. Configs (eslint + stryker)
5. Tests
6. Close issue

---

## Task 10 — Evaluator coverage adapter (null vs zero)

### Goal

Per Decision 11: coverage_delta = `null` for Browser lane, NOT zero. The
Evaluator prompt must treat null as "not applicable" not "low value".

### Sub-tasks

- [ ] 10.1 `agents/evaluator.py` — when building `EvaluatorSignals`, set `coverage_delta = None` if the framework's `coverage_strategy == "skip"`
- [ ] 10.2 `prompts_pkg/prompts._format_evaluator_per_test_block` — render coverage as `"coverage: N/A (browser lane)"` when null
- [ ] 10.3 Update `prompts/evaluator.md` — verdict-priority rules: when coverage_delta is null, skip the coverage rule; don't penalize
- [ ] 10.4 Update `_validate_verdicts` to accept verdicts where `signals_summary.coverage_delta_pct` is `null`
- [ ] 10.5 `tests/test_evaluator.py` — new cases: browser-lane test gets null coverage, prompt block renders correctly, validator accepts null

### Acceptance criteria

- A Playwright subtask emerges from the Evaluator with verdict ∈ `{accept, reject, flag}` based on stability/mutation/lint/semantic ONLY
- The evaluator.md prompt does NOT mention `0% coverage` for browser tests
- 10+ new test cases

### Commit shape (4 commits)

1. Signals bundle null handling
2. Prompt block rendering
3. Validator + evaluator.md verdict-priority update
4. Tests + close issue

---

## Task 11 — Triager update-vs-create + catalog mutation

### Goal

Triager reads the tests catalog, applies the 3-step AC-match lookup,
decides UPDATE-in-place vs CREATE-new, and writes the catalog back.

### Sub-tasks

- [ ] 11.1 `agents/triager.py` — read `context/tests_catalog.json`
- [ ] 11.2 Per accepted/flagged candidate: call `lookup_by_ac` (Task 3); branch UPDATE vs CREATE
- [ ] 11.3 UPDATE path: write to existing `test_file`, increment `generation_version`, update `last_verdict` + `generated_at`
- [ ] 11.4 CREATE path: derive `test_file` from framework's `test_path_conventions`, add new catalog entry
- [ ] 11.5 SKIP path: if `operator_locked: true`, skip + record in triage report
- [ ] 11.6 Write updated catalog back to spec_dir/tests_catalog.json (Triager doesn't touch the AIFactory repo's copy; git_writer does that in v0.2's existing flow)

### Acceptance criteria

- Triager round-trips a catalog: read → 3 candidates with mixed UPDATE/CREATE/SKIP → write produces correct catalog state
- Triage report shows per-candidate decision (UPDATE existing / CREATE new / SKIP locked) with rationale
- 18+ new test cases extending `test_triager.py`

### Commit shape (5 commits)

1. Catalog read at Triager start
2. lookup_by_ac integration
3. UPDATE vs CREATE branching
4. SKIP for operator_locked
5. Tests + close issue

---

## Task 12 — Templates: Playwright + Jest + pytest starter set

### Goal

Ship the per-framework template library that Gen-Functional uses as
starting points. ~5 templates per framework.

### Sub-tasks

- [ ] 12.1 Playwright templates: `login-flow.spec.ts.tmpl`, `form-submit-validation.spec.ts.tmpl`, `api-mocked-flow.spec.ts.tmpl`, `data-loaded-page.spec.ts.tmpl`, `error-state.spec.ts.tmpl`
- [ ] 12.2 Jest templates: `function-pure.test.ts.tmpl`, `function-with-mock.test.ts.tmpl`, `react-component.test.tsx.tmpl`, `async-function.test.ts.tmpl`, `error-boundary.test.tsx.tmpl`
- [ ] 12.3 pytest templates: `function-pure.py.tmpl`, `function-with-mock.py.tmpl`, `fixture-driven.py.tmpl`, `parametrize.py.tmpl`, `async-function.py.tmpl`
- [ ] 12.4 Template engine: Jinja2-like via Python's `string.Template` (simple `${var}` substitution; no logic)
- [ ] 12.5 Each template carries a YAML front-matter `metadata` block: `description`, `requires_target`, `requires_auth`, `vars` list
- [ ] 12.6 `tests/test_templates.py` — each template substitutes valid vars, generates a parseable file (lint or compile check)

### Acceptance criteria

- 15 templates exist (3 frameworks × 5 each)
- Each template instantiates to a file that passes the framework's preflight (Task 9)
- Substitution rejects unknown vars with a clear error
- 20+ test cases

### Commit shape (5 commits)

1. Template engine (simple substitution)
2. Playwright 5 templates
3. Jest 5 templates
4. pytest 5 templates (refining v0.1's implicit templates)
5. Tests + close issue

---

## Task 13 — Skills: pfactory-init / add-test / from-template

### Goal

Author Claude Code skill bundles for engineers to use from their own
sessions (not just the portal).

### Sub-tasks

- [ ] 13.1 `skills/pfactory-init/SKILL.md` — scaffolds `.pfactory.yml` + `.pfactory/tests-catalog.json` in an AIFactory repo; interactive prompts for targets
- [ ] 13.2 `skills/pfactory-add-test/SKILL.md` — engineer adds ONE test to an existing project; runs Gen-Functional sub-flow locally without the full pipeline
- [ ] 13.3 `skills/pfactory-from-template/SKILL.md` — pick a template by name, fill vars, drop into project
- [ ] 13.4 `skills/handover-to-pfactory/SKILL.md` — UPDATE the v0.1 skill to read the new schema + catalog
- [ ] 13.5 Slash commands wrapping the skills: `commands/pfactory-init.md`, `commands/pfactory-add-test.md`, `commands/pfactory-from-template.md`
- [ ] 13.6 `tests/test_skills.py` — structural check: each skill has valid front-matter + a `description` + `when_to_use` + tool list

### Acceptance criteria

- 4 skills with valid SKILL.md (3 new + 1 updated)
- 3 slash commands that invoke their respective skills
- `pfactory-init` against an empty AIFactory dir creates a valid `.pfactory.yml` + empty catalog
- 12+ structural tests

### Commit shape (5 commits)

1. pfactory-init skill + command
2. pfactory-add-test skill + command
3. pfactory-from-template skill + command
4. handover-to-pfactory skill update for new schema
5. Tests + close issue

---

## Task 14 — Portal endpoints for templates / skills / catalogs

### Goal

The portal grows new REST endpoints exposing the framework registry,
templates, skills, and per-project catalog. The frontend (Task 15) uses
these to render a "Templates" tab and a "Coverage gaps" view.

### Sub-tasks

- [ ] 14.1 `apps/web-server/server/routes/pfactory_frameworks.py` — `GET /api/pfactory/frameworks` (list), `GET /api/pfactory/frameworks/{name}` (descriptor detail)
- [ ] 14.2 `apps/web-server/server/routes/pfactory_templates.py` — `GET /api/pfactory/templates?framework={name}` (list templates), `GET /api/pfactory/templates/{framework}/{name}` (template body)
- [ ] 14.3 `apps/web-server/server/routes/pfactory_skills.py` — `GET /api/pfactory/skills` (list skill bundles); reads from `skills/` dir
- [ ] 14.4 Extend existing `pfactory_tasks.py`: `GET /api/pfactory/tasks/{spec_id}/catalog` — serves the spec's tests_catalog.json
- [ ] 14.5 `tests/test_pfactory_routes_frameworks.py`, `..._templates.py`, `..._skills.py` — using the same FastAPI-shim pattern as v0.1 (no fastapi install required for tests)
- [ ] 14.6 `verify-fork.sh` allowlist updates

### Acceptance criteria

- 7 new endpoints respond correctly for happy paths
- 404 on missing framework / template / skill
- 400 on path-traversal (same regex as v0.1's spec_id validator)
- 30+ test cases

### Commit shape (6 commits)

1. Frameworks endpoints
2. Templates endpoints
3. Skills endpoints
4. Catalog endpoint on existing task routes
5. Tests
6. Allowlist + close issue

---

## Task 15 — LaneStatusGrid reskin + migration CLI (closes v0.2)

### Goal

Final task: ship the frontend reskin (5 new lane cards), the `pfactory init`
+ `pfactory migrate v0_1_catalog` CLI commands, and update CHANGELOG +
tag v0.2.0.

### Sub-tasks

- [ ] 15.1 `LaneStatusGrid.tsx` — replace SAST/DAST/Fuzz cards with Browser/API/Integration; keep Functional → Unit + Mutation
- [ ] 15.2 New icons + colors per lane
- [ ] 15.3 `apps/backend/cli/pfactory_init.py` — interactive scaffolder for `.pfactory.yml` + empty catalog
- [ ] 15.4 `apps/backend/cli/pfactory_migrate.py` — `migrate v0_1_catalog` walks `~/.pfactory/workspaces/*/specs/*/` and consolidates per-task test entries into a single per-repo `.pfactory/tests-catalog.json`
- [ ] 15.5 CLI entrypoint: `python -m pfactory.cli init` + `python -m pfactory.cli migrate v0_1_catalog`
- [ ] 15.6 Update CHANGELOG.md with v0.2.0 section (mirror v0.1.0-mvp's CHANGELOG structure)
- [ ] 15.7 Update README.md, docs/index.md, docs/architecture.md status lines: "v0.2 in progress" → "v0.2 released"
- [ ] 15.8 Tag `v0.2.0` + create GitHub Release
- [ ] 15.9 Close all 15 task issues + this meta-issue

### Acceptance criteria

- Frontend: LaneStatusGrid renders 5 new cards; test suite green
- `python -m pfactory.cli init` against a new repo creates valid scaffolding
- `python -m pfactory.cli migrate v0_1_catalog` against a v0.1 workspace consolidates correctly
- CHANGELOG matches v0.1's quality
- `v0.2.0` tag exists + GitHub Release published
- 18+ new tests

### Commit shape (5 commits)

1. LaneStatusGrid reskin + frontend tests
2. pfactory init CLI
3. pfactory migrate CLI
4. CHANGELOG + README + docs updates
5. Tag + release + close v0.2 epic

---

---

## Task 16 — Test evidence capture + portal viewer (closes v0.2)

> **Cross-cutting.** Per Decision 12 in the spec: screenshots + video +
> trace + network HAR as test evidence for human review. Touches Tasks 8
> (Browser runtime), 11 (Triager), 14 (Portal). Lands LAST so it sees all
> the upstream contracts settled.

### Goal

Capture, store, serve, and link evidence artifacts (screenshots / video /
trace / HAR / request-response logs) so human reviewers can see what
PFactory generated running, before they trust it.

### Sub-tasks

- [ ] 16.1 **Playwright config integration** — auto-emit screenshots
  on-failure + video retain-on-failure + trace on-first-retry. Configurable
  via `.pfactory.yml`:

  ```yaml
  evidence_policy:
    browser:
      screenshot: on-failure       # always | on-failure | never
      video: retain-on-failure     # always | retain-on-failure | never
      trace: on-first-retry        # always | on-first-retry | never
    api:
      record_http: always          # always | on-failure | never
    retention:
      failures: forever
      flagged: 90_days
      passing: 7_days
      size_cap_per_task: 500MB
  ```

- [ ] 16.2 **Evidence file layout** — runner writes to
  `/scratch/evidence/<test_id>/{screenshots,video.webm,trace.zip,network.har}`;
  Executor copies to `spec_dir/findings/evidence/<test_id>/`
- [ ] 16.3 **API/Integration evidence wrapper** — `apps/backend/agents/evidence/http_recorder.py`:
  ```python
  with record_http_to_har(spec_dir, test_id):
      # Test code runs; all outbound HTTP recorded into a .har file
  ```
- [ ] 16.4 **Evidence schema in catalog** — extend `CatalogEntry`:
  `last_evidence_run_id`, `evidence_urls: dict[str, str|list[str]]`
- [ ] 16.5 **Retention enforcer** — `apps/backend/agents/evidence/retention.py`:
  per-spec_dir cron-style sweep that prunes based on `evidence_retention` policy
- [ ] 16.6 **Triager PR comment update** — `agents/triager.py` Markdown
  renderer emits evidence-links table per accepted/flagged/rejected test
- [ ] 16.7 **Portal endpoint** —
  `GET /api/pfactory/tasks/<spec_id>/evidence/<test_id>/<artifact>` serves
  raw bytes; content-type via file extension (png, webm, zip, har, jsonl)
- [ ] 16.8 **Portal "Evidence" tab** in `PFactoryTaskDetail.tsx`:
  thumbnail strip for screenshots, inline HTML5 video player, download
  buttons for trace + HAR
- [ ] 16.9 **Tests:**
  - `tests/test_evidence_capture.py` — mocked runner emits per-spec evidence layout
  - `tests/test_evidence_retention.py` — policy-driven pruning
  - `tests/test_triager_evidence_links.py` — PR comment includes evidence URLs
  - `tests/test_pfactory_routes_evidence.py` — portal endpoint serves correctly
  - Frontend: `PFactoryTaskDetail` "Evidence" tab tests

### Acceptance criteria

- A Playwright failure produces: screenshot.png + video.webm + trace.zip
  in `spec_dir/findings/evidence/<test_id>/`
- An API test produces: network.har with all outbound HTTP calls captured
- Triage report's PR comment links work (verified via test client GET on
  each link)
- Portal Evidence tab renders thumbnails + plays video inline
- Retention policy prunes a "passing" test's evidence after the configured
  window (test uses tmp dir + freezegun to simulate time passing)
- 40+ new tests (10 backend per area × 4 areas)

### Commit shape (6 commits)

1. Evidence layout + Playwright config integration
2. HTTP HAR recorder for API/Integration lanes
3. Catalog schema extension + retention enforcer
4. Triager PR-comment evidence-links rendering
5. Portal endpoint + PFactoryTaskDetail Evidence tab
6. Tests + close issue + close v0.2 epic

---

## Risks + execution notes

### Critical-path risk

Tasks 0 → 1/2/3 → 4 → 5 → 6 is sequential and can't be parallelized. That's
~6 tasks × 5 commits avg = **30 commits on the critical path**. At v0.1's
cadence (12 tasks shipped in ~1 day of intensive work), v0.2 is plausibly
**1-2 weeks of focused work** for the critical path + parallel branches.

### Recommended execution

1. **Land Task 0 first.** Don't start anything else until the lane rename
   is in main + green.
2. **Parallelize Tasks 1, 2, 3** as soon as Task 0 lands. Three independent
   schema-and-loader tasks.
3. **Task 4 (snapshotter) is small** — slot it in while waiting for 1/2/3.
4. **Tasks 7 (Docker images) + 12 (templates)** can run in parallel with
   the critical path from Task 5 onwards. Docker image work is mostly
   container-build time, not coding time.
5. **Task 15 is the close-out commit.** It absorbs the CLI helpers + tag
   + release. Don't start it until 11/13/14 are all in.

### Test-volume estimate

| Task | New backend tests | New frontend tests |
|---|---|---|
| 0 | 0 (regression check) | 0 (regression check) |
| 1 | 25 | 0 |
| 2 | 30 | 0 |
| 3 | 25 | 0 |
| 4 | 8 | 0 |
| 5 | 15 | 0 |
| 6 | 20 | 0 |
| 7 | 6 | 0 |
| 8 | 12 | 0 |
| 9 | 35 | 0 |
| 10 | 10 | 0 |
| 11 | 18 | 0 |
| 12 | 20 | 0 |
| 13 | 12 | 0 |
| 14 | 30 | 0 |
| 15 | 5 | 13 |
| 16 | 40 | 8 |
| **TOTAL** | **+311** | **+21** |

End-of-v0.2 totals: **~840 backend + ~133 frontend = ~975 tests**.

### Migration safety

The `Lane.SAST/DAST/FUZZ` deprecation aliases (Task 0) mean v0.1 workspaces
still work through v0.2 with a deprecation warning. v0.3 removes the
aliases entirely.

### Customer-acceptance demo (suggested)

Once v0.2 is tagged, the demo flow for an enterprise prospect:

1. Engineer in their AIFactory repo runs `/handover-to-pfactory`
2. PFactory snapshots the spec, picks Playwright for the UI change + Jest
   for the JS unit logic + pytest for the Python backend
3. docker-compose spins up the app
4. Three lanes' worth of tests generated, all signal-validated
5. **Evidence captured automatically:** screenshots per step, video of each
   failing test, trace.zip for debugging, network HAR for API tests
6. Triage report posted to the PR with cover-by-AC breakdown +
   inline evidence links (engineer clicks → portal opens video at 0:00,
   sees the test exercise the login flow exactly as expected)
7. Engineer accepts → tests committed to feature branch + catalog updated
8. **Three months later:** new dev opens an old PFactory-generated test,
   clicks the catalog entry's "view evidence" link, watches the video
   from the test's first run, understands what it does without reading
   the Playwright TS code

The evidence-driven trust loop is the v0.2 wedge. Without it, "AI-generated
tests" are scary. With it, they're verifiable in 30 seconds per test.

---

## Predecessors + provenance

- **Spec:** [`2026-05-28-enterprise-test-frameworks-design.md`](./2026-05-28-enterprise-test-frameworks-design.md) (11 locked decisions, 80 frameworks catalogued)
- **Interview transcript:** `/super-brainstorm` session 2026-05-28 (architecture + scope + browser-first operating model)
- **Predecessor release:** v0.1.0-mvp tag (12 tasks, 531+112 tests, 4-agent pipeline)
- **Successor:** v0.2 (this plan) — Playwright + Jest + pytest, framework registry, target schema, catalog, platform deliverables
