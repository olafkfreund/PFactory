# Framework Registry

> **Status:** Implemented (Task 1 / #17, PFactory v0.2)
> **Architecture decision:** [Decision 1 — generic prompts + framework descriptor registry](./plans/2026-05-28-enterprise-test-frameworks-design.md)
> **Source:** `apps/backend/framework_registry/`
> **Runtime config:** `frameworks/*/descriptor.yaml`

## What is the registry?

The framework registry is PFactory's single source of truth for per-framework
configuration. Every framework PFactory can generate tests for — `pytest`,
`jest`, `playwright`, and future additions — has a YAML descriptor file at
`frameworks/{name}/descriptor.yaml`. The registry loader reads these at
startup and returns a `dict[str, FrameworkDescriptor]`.

**Why a registry instead of hard-coding?**

PFactory v0.1 hard-coded Python+pytest behavior throughout the codebase.
Adding a second framework would have required touching every agent. The
registry decouples framework-specific knowledge from agent logic:

```
v0.1: Planner → [hard-coded pytest logic] → Gen-Functional → Executor
v0.2: Planner → [registry lookup] → FrameworkDescriptor → Gen-Functional → Executor
```

This means adding `vitest` in v0.3 is a YAML file + a Docker image (Task 7)
— no agent code changes needed.

---

## Quick start

```python
from framework_registry import load_registry, get_descriptor

# Load all descriptors from frameworks/ at repo root
registry = load_registry()
print(list(registry.keys()))  # ['jest', 'playwright', 'pytest']

# Look up a single descriptor
desc = get_descriptor("playwright")
print(desc.coverage_strategy)    # 'skip'
print(desc.runtime.image)        # 'pfactory-runner-playwright:latest'
print([l.value for l in desc.lanes])  # ['browser']

# Validate a single dict (e.g. in a CI check or custom tooling)
from framework_registry import validate_descriptor
descriptor = validate_descriptor(yaml.safe_load(open("frameworks/vitest/descriptor.yaml")))
```

---

## YAML schema

Every `frameworks/{name}/descriptor.yaml` file must conform to this schema.

### Top-level fields

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Framework identifier. Must match the directory name and be unique across all descriptors. E.g. `"playwright"`. |
| `language` | string | yes | Primary programming language. E.g. `"typescript"`, `"python"`. |
| `lanes` | list of strings | yes | PFactory lanes this framework supports. Minimum one. Valid values: `unit`, `browser`, `api`, `integration`, `mutation`. |
| `version_range` | string | yes | PEP 440 specifier string. E.g. `">=1.40,<2.0"`. Stored as-is; use `desc.specifier_set.contains("1.50")` to test membership. |
| `runtime` | mapping | yes | Docker image + entrypoint. See `runtime` section below. |
| `manifest_signals` | list of strings | yes | Files/keys the Planner uses to detect this framework. See `manifest_signals` format below. |
| `test_path_conventions` | list of strings | yes | Glob patterns for where tests live. First match wins when Gen-Functional writes a new file. |
| `coverage_strategy` | string | yes | One of `"lcov"`, `"cobertura"`, `"skip"`. Tells the Evaluator how to parse coverage data for this framework. |
| `context_block` | string | yes | Markdown block injected into the Gen-Functional prompt. Write 5-20 lines of idioms, anti-patterns, and guidance specific to this framework. |
| `templates` | list of strings | no | Template filenames this framework ships (populated in Task 12). Defaults to `[]`. |
| `evaluator_hooks` | list of strings | no | Dotted-path Python references to per-framework Evaluator primitives (populated in Task 9). Defaults to `[]`. |

### `runtime` sub-mapping

| Sub-field | Type | Required | Description |
|---|---|---|---|
| `image` | string | yes | Docker image name. E.g. `"pfactory-runner-playwright:latest"`. Task 7 builds these. |
| `entrypoint` | list of strings | no | Command to invoke the test runner. E.g. `["npx", "playwright", "test"]`. Executor appends the test file path. Defaults to `[]`. |

### `manifest_signals` format

Each signal is a string that describes where to look for evidence that the
framework is installed:

- `"requirements.txt:pytest"` — look for the string `pytest` anywhere in `requirements.txt`
- `"package.json:devDependencies.@playwright/test"` — look for the JSON key path `devDependencies["@playwright/test"]` in `package.json`
- `"playwright.config.ts"` — check if the file exists
- `"pyproject.toml:tool.pytest.ini_options"` — look for the key path `tool.pytest.ini_options` in `pyproject.toml`

The Planner processes signals in order; the first match determines the
framework. Multiple signals increase detection confidence for polyglot repos.

### `coverage_strategy` values

| Value | Meaning | Used by |
|---|---|---|
| `"cobertura"` | Framework emits Cobertura XML via e.g. `--cov-report=xml` | pytest (Python unit) |
| `"lcov"` | Framework emits LCOV via e.g. `--coverage` (nyc) | Jest (TypeScript unit) |
| `"skip"` | Framework cannot emit per-test coverage (browser lane) | Playwright |

When `coverage_strategy = "skip"`, the Evaluator sets `coverage_delta = None`
(not zero). The `evaluator.md` prompt is updated to treat `null` as "not
applicable" so browser tests are not penalised for missing coverage data.
This is Decision 11 in the design spec.

---

## Complete worked example: adding `vitest`

Suppose PFactory v0.3 needs to support [Vitest](https://vitest.dev/) for
TypeScript unit tests (an alternative to Jest). Here's the full procedure:

### Step 1: Create the directory

```bash
mkdir -p frameworks/vitest
```

### Step 2: Write the descriptor

```yaml
# frameworks/vitest/descriptor.yaml
name: vitest
language: typescript
lanes:
  - unit

version_range: ">=1.0,<3.0"

runtime:
  image: pfactory-runner-vitest:latest
  entrypoint:
    - npx
    - vitest
    - run
    - "--reporter=junit"

manifest_signals:
  - "package.json:devDependencies.vitest"
  - "package.json:dependencies.vitest"
  - "vitest.config.ts"
  - "vitest.config.js"
  - "vite.config.ts"     # Vitest shares Vite config

test_path_conventions:
  - "**/*.test.ts"
  - "**/*.spec.ts"
  - "src/**/__tests__/**/*.ts"

templates: []

coverage_strategy: lcov   # Vitest uses @vitest/coverage-v8 or istanbul

context_block: |
  You are generating Vitest tests in TypeScript for the Unit lane.

  Test structure:
    - Use describe() and it() / test() — same as Jest syntax
    - Vitest is API-compatible with Jest for most common patterns

  Mocking:
    - vi.fn() for function mocks (equivalent to jest.fn())
    - vi.spyOn(object, 'method') for spies
    - vi.mock('./module') for module-level mocks
    - vi.useFakeTimers() + vi.runAllTimers() for timer control
    - afterEach(() => vi.restoreAllMocks()) always

  Anti-patterns:
    - Avoid jest.* — always use vi.* equivalents
    - setTimeout in tests → vi.useFakeTimers()

evaluator_hooks: []
```

### Step 3: Validate it

```python
import yaml
from framework_registry import validate_descriptor

with open("frameworks/vitest/descriptor.yaml") as f:
    data = yaml.safe_load(f)

descriptor = validate_descriptor(data)
print(descriptor.name)              # 'vitest'
print(descriptor.coverage_strategy) # 'lcov'
```

Or run the test suite (which includes an end-to-end load check):

```bash
PYTHONPATH=apps/backend apps/backend/.venv/bin/pytest tests/test_framework_registry.py -v
```

### Step 4: Add the Docker image

Create `docker/pfactory-runner-vitest/Dockerfile` with Node 22 + vitest
pre-installed. The image name in the descriptor (`pfactory-runner-vitest:latest`)
must match. Task 7 documents the image-naming convention.

### Step 5: Add evaluator hooks (optional, Task 9)

When Task 9 ships the TypeScript evaluator primitives:

```yaml
evaluator_hooks:
  - "agents.lang_typescript.preflight.ts_preflight"
  - "agents.lang_typescript.flake_lint.ts_flake_lint"
  - "agents.lang_typescript.mutate_probe.ts_mutate_probe"
```

---

## Error reference

`validate_descriptor` raises `FrameworkDescriptorError(field, reason)` on the
first validation failure it encounters. Common errors and how to fix them:

| Error | `field` | `reason` | Fix |
|---|---|---|---|
| Missing field | `"name"` | `"required field is missing"` | Add the field to the YAML |
| Empty name | `"name"` | `"must not be empty or whitespace-only"` | Write a non-empty string |
| Invalid specifier | `"version_range"` | `"invalid PEP 440 specifier: 'v1.40'"` | Use PEP 440 format: `">=1.40"` not `"v1.40"` |
| Unknown lane | `"lanes"` | `"unknown lane 'jest'; valid lanes are: ..."` | Use a valid lane name |
| Bad coverage | `"coverage_strategy"` | `"'v8' is not a valid coverage strategy"` | Use one of `lcov`, `cobertura`, `skip` |
| Bad glob | `"test_path_conventions"` | `"glob pattern '...' is malformed"` | Fix the glob syntax |
| Missing runtime image | `"runtime.image"` | `"required field is missing or empty"` | Add `image:` under `runtime:` |

`FrameworkDescriptorError` has two attributes:

```python
try:
    validate_descriptor(bad_data)
except FrameworkDescriptorError as e:
    print(e.field)   # e.g. "coverage_strategy"
    print(e.reason)  # e.g. "'nonsense' is not a valid coverage strategy; ..."
```

`load_registry` raises `FrameworkRegistryError` (a `RuntimeError`) for:

- `"frameworks directory does not exist: /path/to/frameworks"` — the dir is missing
- `"duplicate framework name 'pytest': found in both ..."` — two dirs have the same `name:` field

---

## How downstream agents consume the descriptor

### Planner (Task 5, #21)

The Planner injects a **framework registry summary** into its prompt — a table
of (name, language, lanes, manifest_signals) for all registered descriptors.
When emitting a subtask, it picks the best (language, framework) pair for each
acceptance criterion and attaches it to the subtask's schema fields.

Post-session, the Planner validator checks every subtask's `(language, framework)`
pair against the registry — rejecting, for example, `(java, playwright)` since
the registry has no Java/Playwright entry.

### Gen-Functional (Task 6, #22)

For each subtask, Gen-Functional calls:

```python
from framework_registry import get_descriptor

desc = get_descriptor(subtask.framework)
# 1. Inject desc.context_block into the generation prompt
# 2. Derive the output file path from desc.test_path_conventions[0]
# 3. Use desc.runtime.image for the Executor's DockerRunner.run()
```

The `context_block` is the most important field: it teaches the LLM the
framework's idioms, anti-patterns, and selector strategy.

### Evaluator (Task 10, #26)

The Evaluator checks `desc.coverage_strategy`:

- `"cobertura"` or `"lcov"` → parse the coverage file, compute delta
- `"skip"` → set `coverage_delta = None` in `EvaluatorSignals`

The `evaluator.md` prompt receives `coverage: N/A (browser lane)` instead of
a percentage, preventing browser tests from being scored as low-value.

---

## Testing the registry

```bash
# Run the full registry test suite (45 cases)
PYTHONPATH=apps/backend apps/backend/.venv/bin/pytest tests/test_framework_registry.py -v

# Quick smoke test — passes if all 3 descriptors validate
PYTHONPATH=apps/backend python3 -c "
from framework_registry import load_registry
from pathlib import Path
r = load_registry(Path('frameworks'))
assert set(r) == {'playwright', 'jest', 'pytest'}
print('OK:', list(r))
"
```

---

## File layout

```
frameworks/                       ← repo root
├── playwright/
│   └── descriptor.yaml           ← Browser lane (TypeScript, coverage=skip)
├── jest/
│   └── descriptor.yaml           ← Unit lane (TypeScript, coverage=lcov)
└── pytest/
    └── descriptor.yaml           ← Unit lane (Python, coverage=cobertura)

apps/backend/
└── framework_registry/
    ├── __init__.py               ← Public API re-exports
    ├── descriptor.py             ← FrameworkDescriptor + RuntimeSpec frozen dataclasses
    ├── validator.py              ← validate_descriptor() + FrameworkDescriptorError
    └── loader.py                 ← load_registry() + get_descriptor() + FrameworkRegistryError
```

---

## See also

- [v0.2 design spec](./plans/2026-05-28-enterprise-test-frameworks-design.md) — Decision 1, Decision 11
- [v0.2 task plan](./plans/2026-05-28-enterprise-test-frameworks-tasks.md) — Task 1 sub-tasks
- `apps/backend/tools/runners/lang_registry.py` — the v0.1 sibling that maps `(language, lane)` to a tool; the registry is the richer per-framework successor
