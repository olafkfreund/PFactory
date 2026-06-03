# PFactory v0.2+: Enterprise functional-test framework support

> **Status:** Design — pending user review
> **Date:** 2026-05-28
> **Authored via:** `/super-brainstorm` (10 locked architectural decisions)
> **Successor to:** [v0.1.0-mvp walking skeleton](https://github.com/olafkfreund/PFactory/releases/tag/v0.1.0-mvp)

## Summary

PFactory v0.1.0-mvp ships a single-framework, single-language walking skeleton:
**Python + pytest, unit lane only**. This design extends PFactory to support
the **enterprise functional-test landscape** across many languages and
frameworks. The architecture is **browser-first, API/integration-second,
unit-last** — the inverse of the industry's "unit-test generation" default.
Generated tests persist into the AIFactory repo with a versioned catalog,
and the platform ships reusable templates / skills / commands / agent
definitions for both the portal and engineers.

The v0.2 release ships **Playwright + Jest + pytest** as the wedge. Java
(JUnit 5), .NET (xUnit), Go, Ruby, and the broader framework universe land
in v0.3+ using the same registry-based extension shape.

---

## Scope

### In scope (functional + feature testing)

| Category | Examples | Why |
|---|---|---|
| **Browser E2E** ⭐ headline | Playwright, Cypress, Selenium, Appium | "Use browser automation to test everything" — operator-stated priority |
| **API testing** | Postman/Newman, REST Assured, Karate, supertest, Tavern | "Test all API, connection" |
| **Integration** | TestContainers, WireMock, MockServer | "Features, gates" — real deps without browser |
| **Contract** | Pact, Karate | Microservice integration |
| **Feature flag / gate** | LaunchDarkly, GrowthBook, OpenFeature SDKs | "Gates" — testing rollouts/dark launches/A-B variants |
| **Unit** | pytest, JUnit 5, xUnit, Jest, Vitest, Go testing | Last-resort modality |
| **Mutation** | mutmut, Stryker, PIT | Validates the strength of the functional tests we generate |
| **BDD style overlay** | Cucumber, SpecFlow, Behave | How enterprise QA teams write tests — a *style* on top of the above |

### Out of scope (operator-stated)

- ❌ SAST / static security analysis — "we have pipelines and other controllers for that"
- ❌ DAST / dynamic security testing
- ❌ Dependency / secrets / container / IaC scanning
- ❌ Performance / load testing (JMeter, k6, Gatling, Locust)
- ❌ Pure security fuzzing (property-based testing IS kept for functional strength)

### Deferred (could be revisited in v0.4+)

- ⏸ Visual regression (Percy, Chromatic, Playwright screenshots)
- ⏸ Accessibility testing (axe-core, pa11y, Lighthouse CI)
- ⏸ Mobile E2E (Appium) beyond Playwright's mobile emulation

---

## Operating model — the test-modality priority

PFactory's Planner walks down this priority for every feature it tests:

```
  1. Can this be exercised through a BROWSER?      → Playwright/Cypress E2E
  2. Is it an API/RPC surface?                     → API test (Postman / REST Assured / etc.)
  3. Is it a cross-service / feature-flag gate?    → Integration test (TestContainers + flag injection)
  4. Otherwise                                     → Unit test (pytest / JUnit / Jest)
```

**Mutation lane** is orthogonal: validates that whatever tests were generated
actually catch regressions.

This is **opposite to the industry default** (Diffblue, Meta TestGen-LLM, Qodo
all start with unit tests because they're easier to generate). PFactory bets
on the outside-in tests humans hate writing.

---

## Architecture overview

The 4-agent pipeline (Planner → Gen-Functional → Executor → Evaluator → Triager)
stays. Five things change:

1. **Each agent is parameterized over (language, framework)** via a registry
2. **Lane spine is replaced**: Browser · API · Integration · Unit · Mutation
3. **AIFactory projects gain `.pfactory.yml`** declaring target services + runtime
4. **AIFactory repos grow `.pfactory/tests-catalog.json`** for cross-run continuity
5. **PFactory ships reusable platform artifacts** (templates, skills, commands, agent definitions)

```
                              ┌─────────────────────────────────────────────┐
                              │  Framework Registry (per-framework YAML)    │
                              │  - detection rules                          │
                              │  - runner image                             │
                              │  - test path conventions                    │
                              │  - templates                                │
                              │  - evaluator hooks (preflight/lint/mutate)  │
                              └────────────┬────────────────────────────────┘
                                           │ context injection
       AIFactory repo                      ▼
       (.pfactory.yml,    ─────►  Planner (generic prompt)
        .pfactory/                    ↓ emits subtasks each carrying (language, framework, lane, target)
        tests-catalog.json)           ↓
       (.claude/skills/                ── Gen-Functional (generic prompt + framework descriptor) ──┐
        +commands/                                                                                 │
        +agents/)                       ↓ writes test file in framework conventions                 │
                                        ↓                                                            │
                                        Executor (per-framework Docker image)                       │
                                        ↓ runs tests against target (URL / k8s / docker-compose)    │
                                        ↓                                                            │
                                        Evaluator (5 signals; per-language primitives)              │
                                        ↓ verdicts.json                                              │
                                        ↓                                                            │
                                        Triager (dedup + rank + render + git_writer + pr_comment)   │
                                        └────► AIFactory repo (tests + tests-catalog.json updates) ←┘
```

---

## Decision tree (the 10 locked choices)

| # | Decision | Value | Rationale |
|---|---|---|---|
| 1 | Agent parameterization | Generic prompts + framework descriptor registry | Scales to 100+ frameworks; bet on LLM's broad knowledge |
| 2 | Lane structure | Browser · API · Integration · Unit · Mutation | Aligns to operating-model priority; 5 cards in `LaneStatusGrid` |
| 3 | Detection timing | Planner picks per-subtask, polyglot native | Enterprise repos are polyglot; subtask-level granularity |
| 4 | Detection method | Manifest+config sniff first, LLM fallback | Fast/deterministic for 90% case, flexible for the rest |
| 5 | Browser app runtime | `.pfactory.yml` + docker-compose (default), preview URL (override) | Maintainable; matches modern dev infra |
| 6 | Test artifact lifecycle | `.pfactory/tests-catalog.json` (in AIFactory repo) + filesystem hybrid | Cross-run continuity; explicit update-vs-create |
| 7 | Artifact packaging | Monorepo typed dirs + framework-bundled extras + project-local `.pfactory/` | 3-tier override; ships in PFactory, project-local for team specifics |
| 8 | v0.2 cut | Playwright + Jest + pytest | Browser-first wedge; two languages; three frameworks; tractable |
| 9 | Browser selector strategy | Role-based → data-testid → CSS (in order) | Robust to redesigns; encourages best practice |
| 10 | Target addressing | Single `.pfactory.yml` with `targets:` array | Subtasks reference by name; auth via env-var indirection |
| 11 | Coverage signal | Per-framework `coverage_strategy` field; Browser lane = `skip` (null, not zero) | Browser tests can't emit per-test coverage XML; Evaluator must treat null as N/A so browser tests aren't penalised |
| 12 | Test evidence capture | Screenshots + video + trace + network HAR (per-framework config); workspace-stored + portal-served; failures kept indefinitely, passes purged after retention window | Human reviewers need to SEE what PFactory generated. "I don't trust an AI test until I've watched it run" is the #1 adoption blocker. |

---

## Framework descriptor schema

The central artifact of the new architecture. **Every framework** PFactory
supports gets a YAML descriptor at `frameworks/{name}/descriptor.yaml`.

```yaml
# frameworks/playwright/descriptor.yaml
name: playwright
version_range: ">=1.40.0"
languages: [typescript, javascript, python, csharp, java]
lanes: [browser]                           # which PFactory lanes this serves
default_lane: browser

# How the Planner detects this framework in a project
detection:
  manifests:
    - file: package.json
      json_path: "$.devDependencies['@playwright/test']"
    - file: package.json
      json_path: "$.dependencies['@playwright/test']"
  configs:
    - file: playwright.config.ts
    - file: playwright.config.js
  test_files:
    - pattern: "**/*.spec.ts"
    - pattern: "**/*.spec.js"
    - pattern: "tests/**/*.ts"

# Where Gen-Functional writes new tests by default
test_path_conventions:
  default: tests/e2e/
  fallback: e2e/
  filename_pattern: "{feature_slug}.spec.ts"

# Docker image carrying browsers + framework
runtime:
  image: pfactory-runner-playwright:latest
  invocation: "npx playwright test {test_file_path}"
  args:
    - "--reporter=junit"
    - "--reporter-output=/scratch/junit.xml"

# How this framework reports coverage (drives Evaluator's coverage_delta hook)
coverage_strategy: skip      # one of: cobertura | v8 | nyc | jacoco | coverlet | skip
                             # 'skip' = framework doesn't emit per-test
                             # coverage; Evaluator treats coverage_delta as
                             # null (NOT zero) for these tests.

# Hooks into Evaluator's per-language primitives
evaluator_hooks:
  preflight: typescript_tsc      # tsc --noEmit imports resolve
  flake_lint: eslint_pfactory    # ESLint config tuned for E2E flake patterns
  mutate:    stryker             # Stryker config for one-mutation probe

# Selectors the Gen-Functional prompt prefers
selector_strategy:
  prefer: role_based
  fallback_chain: [data_testid, css]

# Templates (Jinja-style {{vars}} interpolation)
templates:
  - name: login-flow
    file: templates/login-flow.spec.ts.tmpl
    description: Standard login + protected-page-access scenario
    requires_target: web
    requires_auth: true
  - name: form-submit-validation
    file: templates/form-submit-validation.spec.ts.tmpl
    description: Submit form, assert validation + success state
  - name: api-mocked-flow
    file: templates/api-mocked.spec.ts.tmpl
    description: E2E with route mocking via page.route()

# Catalog metadata fields this framework needs to track
catalog_fields:
  - browsers_tested              # chromium / firefox / webkit
  - target_ref                   # which target from .pfactory.yml
  - viewport                     # mobile vs desktop

# LLM context block — appended to Gen-Functional prompt
context_block: |
  You are generating Playwright tests in TypeScript.

  Idioms to use:
    - page.getByRole('button', { name: 'Submit' }) — role-based, preferred
    - page.getByTestId('foo') — when the app has data-testid attrs
    - expect(page.locator(...)).toBeVisible() — auto-wait, never page.waitForTimeout
    - Use storageState for auth (see auth context); never login via UI in tests

  Anti-patterns to avoid:
    - page.waitForTimeout(N) — replace with auto-wait expectations
    - Brittle CSS selectors when role-based works
    - UI-driven login (use programmatic auth or storageState instead)
    - Hard-coded test data (use fixtures or .pfactory.yml seed scripts)
```

### Per-language Evaluator primitives

The Evaluator's 5 signals are mostly language-agnostic, but 3 primitives are
Python-specific in v0.1 and need per-language analogs:

| Primitive | Python (v0.1) | TypeScript/JS (v0.2) | Java (v0.3) | Go (v0.4+) |
|---|---|---|---|---|
| `preflight` | subprocess `python -c "import X"` | `tsc --noEmit` (imports + types resolve) | `javac -nowarn` | `go build ./...` |
| `flake_lint` | AST scan: 5 patterns | ESLint w/ pfactory ruleset | Checkstyle + PMD ruleset | `go vet` + custom rules |
| `mutate_probe` | AST mutates Eq/Bool/Int | **Stryker** (one mutation, dry-run) | **PIT** (one mutation) | gomutator or skip |

**Stability** + **LLM semantic relevance** are language-agnostic and unchanged.

**Coverage delta** is language-agnostic in mechanism (Cobertura XML parser)
but **not framework-agnostic**: it requires the runner to emit a coverage
XML file. Per lane:

| Lane | Coverage signal | Why |
|---|---|---|
| Unit (pytest, Jest, JUnit, xUnit, Go test) | ✅ Cobertura via `--cov` / `nyc` / JaCoCo / Coverlet / `go test -cover` | Native support across all major frameworks |
| API (supertest, REST Assured, httpx) | ✅ Cobertura via the unit framework underneath | Same coverage tooling as unit |
| Integration (TestContainers) | ✅ Cobertura via unit framework | Same |
| **Browser (Playwright, Cypress)** | ❌ **N/A** | Browser tests instrument the *served app*, not the test code; coverage instrumentation requires runtime probes (V8 coverage API + nyc) that don't fit the per-test model |
| Mutation | N/A (different lane semantics) | Mutation result IS the verdict |

For Browser lane tests, the Evaluator receives `coverage_delta = null` in
the per-test signal bundle. The `evaluator.md` prompt is updated to
interpret `null` as **"not applicable for this lane"** (NOT "zero"). The
verdict-priority rules in `evaluator.md` skip the coverage_delta rule when
it's null and rely on the remaining 4 signals.

> **Implementer note** — Browser lane tests scored as low-value just
> because coverage was zero is the #1 risk this fix prevents. See Task 10
> in the v0.2 task list.

---

## Target / service addressing — `.pfactory.yml` schema

A versioned config file at the AIFactory repo root that declares **where the
system-under-test lives** + how to start it locally. Subtasks reference
targets by name.

```yaml
# AIFactory-repo/.pfactory.yml
version: 1

# Default target for all subtasks unless they override
default_target: web-local

# Targets the Planner can reference for Browser / API / Integration subtasks
targets:

  # === HTTP target (browser or REST API) ===
  - name: web-staging
    type: http
    url: https://staging.example.com
    healthcheck:
      path: /healthz
      expect_status: 200
      timeout_seconds: 30
    auth:
      type: bearer
      token_env: STAGING_TOKEN
    selectors_hint: data_testid   # this app has data-testid throughout

  # === API target (REST) ===
  - name: api-staging
    type: http
    base_url: https://api.staging.example.com
    openapi_spec: openapi.yaml      # for API test generation
    auth:
      type: oauth2_client_credentials
      token_url: https://auth.example.com/oauth/token
      client_id_env: API_CLIENT_ID
      client_secret_env: API_CLIENT_SECRET
      scope: read:resources

  # === Kubernetes target (port-forward) ===
  - name: in-cluster
    type: kubernetes
    context: my-prod-cluster
    namespace: staging
    service: frontend
    port: 80
    port_forward: true
    auth:
      type: serviceaccount
      kubeconfig_env: KUBECONFIG

  # === Local docker-compose (Decision 5 default) ===
  - name: web-local
    type: docker-compose
    compose_file: docker-compose.yml
    services_to_start: [frontend, backend, db]
    wait_for:
      - url: http://localhost:3000
        path: /
    auth:
      type: test_user
      seed_command: "scripts/seed-test-user.sh"

  # === Feature flag overlay (v0.3+) ===
  - name: feature-flag-overlay
    type: feature_flag
    provider: launchdarkly
    sdk_key_env: LD_SDK_KEY
    test_user_targeting:
      user_id: pfactory-test-user
      attributes:
        plan: enterprise

# Test data seeding + reset
test_data:
  seed_command: "scripts/seed.sh"
  reset_command: "scripts/reset.sh"
  isolation: per_task         # per_task | per_subtask | none

# Where PFactory's generated tests land (overrides framework descriptor defaults)
test_paths:
  browser: tests/e2e/
  api: tests/api/
  integration: tests/integration/
  unit: tests/unit/
```

---

## Test artifact lifecycle — catalog + filesystem hybrid

### `.pfactory/tests-catalog.json` (in AIFactory repo)

```json
{
  "version": 1,
  "updated_at": "2026-05-28T12:00:00Z",
  "tests": [
    {
      "test_id": "ac1-login-flow",
      "test_file": "tests/e2e/login-flow.spec.ts",
      "framework": "playwright",
      "lane": "browser",
      "language": "typescript",
      "covers_acs": ["AC#1: User can log in with valid credentials"],
      "generated_at": "2026-05-28T10:30:00Z",
      "generated_by_task": "042-session-expiry",
      "last_verdict": "accept",
      "browsers_tested": ["chromium"],
      "target_ref": "web-staging",
      "operator_locked": false
    }
  ]
}
```

### Update-vs-create policy (Triager)

```
For each candidate test from Gen-Functional:
  matches = catalog.lookup_by_ac(candidate.ac_id)
  if matches and matches[0].operator_locked:
    → SKIP (operator marked "don't regenerate")
  elif len(matches) == 1:
    → UPDATE in place (same file path, increment generation_version)
  elif len(matches) > 1:
    → flag as catalog ambiguity; pick the most-recent + warn the operator
      in the triage report
  else:
    → CREATE new file in framework-conventional path
    → ADD entry to catalog
```

### AC-match algorithm (`catalog.lookup_by_ac`)

The catalog's `covers_acs` is an array of strings, not a primary key. The
lookup uses a **deterministic 3-step match** in priority order:

```
def lookup_by_ac(catalog, candidate_ac: str) -> list[CatalogEntry]:
    # 1. Exact match — best signal
    exact = [e for e in catalog.tests if candidate_ac in e.covers_acs]
    if exact:
        return exact

    # 2. AC-id prefix match — covers "AC#1: …" style where prefix is stable
    #    candidate_ac="AC#1: login expiry" matches stored "AC#1: login flow"
    ac_id = candidate_ac.split(':', 1)[0].strip()  # e.g. "AC#1"
    if ac_id:
        prefix = [e for e in catalog.tests
                  if any(s.startswith(ac_id) for s in e.covers_acs)]
        if prefix:
            return prefix

    # 3. Empty — no match
    return []
```

Embedding-similarity matching is **explicitly out of scope** for v0.2 — it
would couple the Triager to an embedding model and add a network call per
catalog lookup. Exact + prefix match handles >95% of real-world cases (the
AC-id is the stable handle); the operator handles the rest via
`operator_locked` flags or manual edits.

### Existing-test discovery (Planner)

Before emitting subtasks, the Planner:
1. Reads `.pfactory/tests-catalog.json` (authoritative)
2. Walks `test_paths` from `.pfactory.yml` looking for **untracked** test files
3. For untracked files, LLM-judges: which AC does this cover?
4. Adds discovered tests to the working catalog
5. Subtasks for an AC reference the existing test (UPDATE path) if any

---

## Platform deliverables

The PFactory repo grows top-level directories for **reusable artifacts**
that the portal AND engineers consume:

```
PFactory/
├── frameworks/                  # Framework registry (Decision 1)
│   ├── playwright/
│   │   ├── descriptor.yaml
│   │   └── templates/
│   │       ├── login-flow.spec.ts.tmpl
│   │       ├── form-submit-validation.spec.ts.tmpl
│   │       └── api-mocked.spec.ts.tmpl
│   ├── jest/
│   ├── pytest/
│   ├── junit5/          # v0.3
│   └── ...
│
├── templates/                   # Cross-framework templates
│   ├── pfactory-yml/
│   │   └── starter.yaml.tmpl         # `.pfactory.yml` starter for new projects
│   └── catalog/
│       └── starter.json.tmpl         # `.pfactory/tests-catalog.json` starter
│
├── skills/                      # Claude Code skill bundles
│   ├── handover-to-pfactory/         # already shipped
│   ├── pfactory-add-test/            # NEW: engineer adds ONE test
│   ├── pfactory-from-template/       # NEW: pick template, fill vars, drop
│   ├── pfactory-strengthen-tests/    # NEW: mutation-probe an existing test
│   ├── pfactory-explain-coverage/    # NEW: query catalog, show gaps
│   └── pfactory-init/                # NEW: scaffold .pfactory.yml + catalog
│
├── commands/                    # Slash commands (often wrap skills)
│   ├── pfactory-generate-e2e.md
│   ├── pfactory-generate-api.md
│   ├── pfactory-from-template.md
│   └── pfactory-explain-coverage.md
│
├── agents/                      # Distributable agent definitions
│   ├── planner.md               # Repackaged for engineer use in their own Claude
│   ├── gen-functional.md
│   ├── evaluator.md
│   └── triager.md
│
└── apps/backend/                # Internal pipeline (unchanged structure)
```

### Authoring layers (Decision 7)

| Layer | Where | Who edits | When merged |
|---|---|---|---|
| **Shipped** | PFactory repo `frameworks/`, `templates/`, etc. | PFactory maintainers via PR | At PFactory release |
| **Project-local** | AIFactory repo `.pfactory/templates/`, etc. | Team via PR to their own repo | Per AIFactory commit |
| **User-local** | `~/.pfactory/templates/`, etc. | Individual engineer | Per engineer's preferences |

Portal + Gen-Functional merge all 3 at runtime; project-local overrides
shipped; user-local overrides project-local.

---

## Browser lane — concrete implementation details

### Selectors (Decision 9)

The Gen-Functional prompt for the Browser lane MUST instruct:

```
Selector priority (try each in order; stop at first match):
  1. page.getByRole('<role>', { name: '<accessible-name>' })
     ← always prefer this
  2. page.getByTestId('<id>')
     ← when the app has data-testid attributes (check existing markup)
  3. page.getByLabel('<label>'), page.getByPlaceholder('<text>')
     ← for form inputs
  4. page.locator('css-selector')
     ← LAST RESORT; emit a TODO comment for the human reviewer

NEVER generate XPath selectors.
NEVER generate `page.locator('.brittle-css-class')` without a TODO.
```

### Auth + storage state

Browser tests share an auth fixture across the suite via Playwright's
`storageState`:

```typescript
// tests/e2e/auth-setup.ts  (generated once per project by `pfactory-init` skill)
import { test as setup } from '@playwright/test';

setup('authenticate', async ({ page }) => {
  // .pfactory.yml's auth block tells us HOW
  await page.goto(process.env.PFACTORY_WEB_URL);
  await page.getByLabel('Email').fill(process.env.PFACTORY_TEST_EMAIL);
  await page.getByLabel('Password').fill(process.env.PFACTORY_TEST_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL('**/dashboard');
  await page.context().storageState({ path: '.auth/state.json' });
});

// All other tests reuse `.auth/state.json` via playwright.config.ts
```

### Page Object pattern

When the catalog shows >1 test exercising the same page, Gen-Functional
emits a Page Object alongside:

```typescript
// tests/e2e/pages/LoginPage.ts  (template-driven)
import { Page, Locator } from '@playwright/test';

export class LoginPage {
  readonly page: Page;
  readonly emailInput: Locator;
  readonly passwordInput: Locator;
  readonly submitButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.emailInput = page.getByLabel('Email');
    this.passwordInput = page.getByLabel('Password');
    this.submitButton = page.getByRole('button', { name: 'Sign in' });
  }

  async loginAs(email: string, password: string) {
    await this.emailInput.fill(email);
    await this.passwordInput.fill(password);
    await this.submitButton.click();
  }
}
```

### Wait strategy

The flake-lint analog (ESLint config) for the Browser lane bans:

- `page.waitForTimeout(N)` — replace with `expect(...).toBeVisible({ timeout: N })`
- `setTimeout()` in test bodies — replace with `expect.poll()` or auto-wait
- Hardcoded sleeps of any kind

### Cross-browser

v0.2: **Chromium only** in CI; Firefox + WebKit opt-in via `.pfactory.yml`:

```yaml
browser_matrix:
  - chromium      # always
  - firefox       # opt-in
  - webkit        # opt-in
```

### Evidence capture (Decision 12)

The single highest-value differentiator for AI-generated tests is **letting
humans see what was generated, running, before they trust it**. PFactory
captures evidence by default on Browser/API/Integration lanes.

**What's captured per lane:**

| Lane | Screenshots | Video | Trace / HAR | Native? |
|---|---|---|---|---|
| Browser (Playwright) | ✅ auto-on-failure + per-step opt-in | ✅ retain-on-failure (default), always (opt-in) | ✅ trace.zip with screenshots-per-action + network log | Yes, all native |
| Browser (Cypress) | ✅ auto-on-failure | ✅ always (Cypress default) | ⚠️ via plugin | Mostly native |
| API (supertest/REST Assured/httpx) | N/A | N/A | ✅ HTTP request/response log (JSON-lines) | Wrapped via PFactory's `record_http` helper |
| Integration (TestContainers) | N/A | N/A | ✅ container stdout/stderr + HTTP HAR | Wrapped |
| Unit (Jest/pytest/JUnit) | N/A (unless `playwright-component` style) | N/A | ✅ stdout/stderr only | None |
| Mutation | Inherits the test's evidence capture; mutation-failure screenshot retained | | | |

**Per-framework descriptor field:**

```yaml
# frameworks/playwright/descriptor.yaml
evidence:
  screenshot: native           # native | wrapped | none
  screenshot_policy: on-failure   # on-failure | always | never (operator override in .pfactory.yml)
  video: native
  video_policy: retain-on-failure  # always | retain-on-failure | never
  trace: native
  trace_policy: on-first-retry     # always | on-first-retry | never
  network_har: native
```

**Storage location (workspace-first):**

Evidence artifacts land at:

```
~/.pfactory/workspaces/<proj>/specs/<spec>/findings/evidence/
  <test_id>/
    screenshots/
      step-01-form-loaded.png
      step-02-submitted.png
      failure-final-state.png    # only when test failed
    video.webm                   # if video_policy fires
    trace.zip                    # Playwright trace
    network.har                  # network log
    metadata.json                # links + sizes + timings
```

Portal serves these via a new endpoint (Task 14):
`GET /api/pfactory/tasks/<spec_id>/evidence/<test_id>/<artifact>`.

**The catalog references evidence:**

```json
{
  "test_id": "ac1-login-flow",
  "test_file": "tests/e2e/login-flow.spec.ts",
  "last_verdict": "accept",
  "last_evidence_run_id": "run-2026-05-28-1430",
  "evidence_urls": {
    "screenshots": ["step-01-...png", "step-02-...png"],
    "video": "video.webm",
    "trace": "trace.zip"
  }
}
```

**Retention policy (configurable in .pfactory.yml):**

```yaml
evidence_retention:
  failures: forever              # never delete failure evidence
  flagged: 90_days               # keep flagged-test evidence 3 months
  passing: 7_days                # passing tests purged after 7 days
  size_cap_per_task: 500MB       # if exceeded, prune oldest-first
```

**Triager integration (PR comment):**

The triage report links to evidence inline:

```markdown
## Test results

| Test | Verdict | Evidence |
|---|---|---|
| `ac1-login-flow` | ✅ accept | [screenshots](https://portal/.../ac1/screenshots) · [video (32s)](https://portal/.../ac1/video.webm) |
| `ac2-checkout` | ❌ reject | [failure screenshot](https://portal/.../ac2/screenshots/failure.png) · [trace.zip](https://portal/.../ac2/trace.zip) |
| `ac3-search-filter` | ⚠️ flag | [video (18s)](https://portal/.../ac3/video.webm) — see frame at 0:12 for unexpected scroll |
```

**Why this matters for adoption:**

- Reviewer can watch a 30-second video and decide "yes this is the right
  test" in seconds vs reading 200 lines of Playwright TS
- Failures are debuggable WITHOUT reproducing locally — the trace.zip
  contains every DOM snapshot + every network request
- API test "evidence" (request/response JSONs) lets a reviewer verify the
  test actually exercised the contract they care about

---

## API lane — concrete implementation details

The Planner picks a framework based on language:

| Language | Default API framework | Why |
|---|---|---|
| TypeScript/JS | **supertest** + Jest | Fits naturally with Jest, no extra runner needed |
| Python | **httpx** + pytest | Modern, async, plays well with pytest |
| Java | **REST Assured** | De facto enterprise standard |
| .NET | **xUnit + HttpClient** | Idiomatic .NET |
| Go | **net/http/httptest** | Built-in |

OpenAPI spec (declared in `.pfactory.yml` target's `openapi_spec`) drives:
- Endpoint discovery
- Schema validation
- Example payloads (via `$.examples`)

Contract testing (Pact) gets its own subtask type when the Planner detects
microservice topology in `docker-compose.yml`.

---

## Integration lane — concrete implementation details

Uses TestContainers (multi-language) to spin up real dependencies for tests:

```python
# Generated by Gen-Functional in the Integration lane (pytest example)
from testcontainers.postgres import PostgresContainer

def test_user_creation_persists_to_db():
    with PostgresContainer("postgres:16") as pg:
        # Subtask context block tells the LLM the connection string env var
        # for the SUT to point at pg.get_connection_url()
        ...
```

Feature flag testing gets a sub-modality: PFactory injects flag state via
the provider's SDK then exercises the protected code path:

```typescript
// Generated for a LaunchDarkly-gated feature
import { LDClient } from 'launchdarkly-node-server-sdk';

test('new checkout flow activates when feature flag is on', async () => {
  const ld = LDClient.initialize(process.env.LD_SDK_KEY);
  ld.variation('new-checkout-flow', testUser, false /* default */);
  // ... exercise the flag-gated path
});
```

---

## Roadmap

### v0.2 (next release after MVP)

**Theme:** First multi-lane, multi-framework release. Browser-first wedge.

| Lane | Framework | Status |
|---|---|---|
| Browser | Playwright (TS) | NEW |
| Unit | Jest (TS) | NEW |
| Unit | pytest (Python) | Carried from v0.1 |
| API | (deferred to v0.3) | — |
| Integration | (deferred to v0.3) | — |
| Mutation | (deferred to v0.3) | — |

Platform deliverables in v0.2:
- Framework registry skeleton + 3 descriptors
- `.pfactory.yml` schema v1 (`targets:`, `test_paths:`, `test_data:`)
- `.pfactory/tests-catalog.json` schema v1
- New skills: `pfactory-init`, `pfactory-add-test`, `pfactory-from-template`
- Browser app runtime via docker-compose
- `LaneStatusGrid` updated to new lane vocabulary

### v0.3

**Theme:** Add API + Integration lanes. Add Java + .NET.

| Lane | Framework |
|---|---|
| API | Postman/Newman, REST Assured (Java), supertest (TS), httpx (Python) |
| Integration | TestContainers (multi-language), WireMock (Java) |
| Browser | Cypress added (alongside Playwright) |
| Unit | JUnit 5 (Java), xUnit (.NET), Vitest (TS) |
| Mutation | Stryker (TS), PIT (Java) |

Platform deliverables:
- New skills: `pfactory-explain-coverage`, `pfactory-strengthen-tests`
- Per-language Evaluator primitives (tsc-preflight, ESLint-lint, javac-preflight, Checkstyle-lint)
- Multi-target `.pfactory.yml` (kubernetes + http alongside docker-compose)

### v0.4+

**Theme:** Long tail of enterprise frameworks. Go, Ruby, Rust, Kotlin, PHP.
Mobile via Appium. Visual regression (Percy/Chromatic). Accessibility (axe-core).

Each new framework = ~1 descriptor + 1 Docker image + 3-5 templates. Adds in
isolation without touching the rest of the pipeline.

---

## Migration from v0.1.0-mvp

**Breaking changes:**

1. **Lane enum changes**: `Lane.FUNCTIONAL` → `Lane.UNIT`. `Lane.SAST/DAST/FUZZ` → removed (deprecated; emit warning if seen). `Lane.BROWSER/API/INTEGRATION` added.
2. **`LaneStatusGrid` renames**: Functional → Unit, SAST/DAST → Browser/API, Fuzz → Integration.
3. **`prompts/gen_functional.md` → `prompts/gen-functional.md`** + add `{{framework_context}}` placeholder.
4. **`agents/gen_functional.py` → `agents/gen_functional.py` extended** to dispatch on subtask's (language, framework, lane) tuple.
5. **`workspaces/snapshotter.py` extended** to read `.pfactory.yml` if present.
6. **`tests-catalog.json` introduced** at AIFactory repo root.

**Non-breaking:**

- Existing pytest tests generated by v0.1 still work — the registry handles
  the old single-framework world as a default.

**Migration path for v0.1 users:**

```bash
# Run once per AIFactory project after upgrading PFactory
$ pfactory init                      # creates .pfactory.yml + .pfactory/tests-catalog.json
$ pfactory migrate v0_1_catalog      # walks existing tests/, populates catalog
```

---

## Risks and open questions

### Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Playwright Docker image is huge (~1.5GB) | Medium | Cache aggressively in CI; offer a "bring your own image" option |
| Browser tests need a running app — many enterprises don't have docker-compose | Medium | `.pfactory.yml` `targets:` supports external URLs as alternative |
| Auth/secret management in `.pfactory.yml` env-var indirection — risk of leaks | High | Document strict policy: never log target tokens, redact in PR comments |
| Cross-language Evaluator primitives drift from each other | Medium | Per-language test suite for each primitive; common shape required |
| Framework registry becomes a coordination bottleneck | Low | Project-local `.pfactory/frameworks/` for team extensions |
| LLM hallucinates Playwright APIs (especially newer ones) | Medium | Tight context block + reference templates; nightly LLM eval on a known repo |
| BDD style (Cucumber) doesn't map cleanly to subtask shape | Medium | Defer to v0.4; for now Cucumber tests aren't generated by PFactory |

### Open questions

1. ~~Should PFactory generate Page Objects from day 1, or only when >1 test exists on the same page?~~ → **DECIDED in this spec**: Page Objects emitted by Gen-Functional only when the catalog has >1 test on the same page (template ships in v0.2 either way).
2. **How does PFactory handle authentication for `.pfactory.yml` targets that need OAuth?** → Document the env-var indirection pattern; defer interactive OAuth flows to v0.3.
3. **What's the test isolation default — per-task, per-subtask, none?** → Default per-task; declarable in `.pfactory.yml`.
4. **Should mutation-probe ALWAYS run for Browser tests?** → No — expensive (re-runs the whole E2E). Sample-based: 1 in N browser tests get mutation-probed.
5. **What's the upgrade story when a framework descriptor changes?** → Version field in descriptor; catalog records `generated_with_descriptor_version`; warn on mismatch.

---

## What v0.2 implementation needs (next steps)

This design is the architectural foundation. The next step is **writing an
implementation plan** that breaks v0.2 into tasks similar to the v0.1 12-task
shape. Tentative task breakdown (~15 tasks):

**Task 0 — Lane rename + breaking-change migration** (must land first; gates
everything else):
- Rename `Lane.FUNCTIONAL` → `Lane.UNIT` in `apps/backend/test_plan/lane.py`
- Add `Lane.BROWSER`, `Lane.API`, `Lane.INTEGRATION` (keep `Lane.MUTATION`)
- Remove deprecated `Lane.SAST`, `Lane.DAST`, `Lane.FUZZ` (with a one-release
  deprecation warning if seen in old plans)
- Update `lane_dispatch.py`: `_MVP_LIT_LANES`, `_LANE_PHASES` dict
- Update `lang_registry.py`: `_LANE_KEYS` tuple
- Update `task_control.py`: `_MVP_LANES` tuple
- Update tests with literal lane lists:
  - `tests/test_test_plan_lane.py` (asserts `["functional","sast","dast","fuzz","mutation"]`)
  - `tests/test_lane_dispatch.py` (asserts `"sast"` raises with `"phase 3"`)
  - `tests/test_lang_registry.py` (iterates `("functional","sast","deps","secrets","mutation")`)
- Update frontend:
  - `LaneStatusGrid.tsx` TypeScript union type
  - `LaneStatusGrid.test.tsx:51` array assertion
  - All UI labels Functional → Unit, etc.

1. **Framework registry data model + loader** — descriptor schema validator
2. **`.pfactory.yml` schema + parser + validator** — including `targets:`,
   `test_paths:`, `test_data:`, `wait_for:` blocks
3. **`.pfactory/tests-catalog.json` schema + read/write helpers** — including
   the AC-match algorithm (see Open Question 2 — must be resolved here)
4. **Snapshotter extended** to read `.pfactory.yml` + catalog
5. **Planner extended** for per-subtask `(language, framework, lane, target)`
6. **Gen-Functional refactored** — generic prompt + framework context injection
7. **Per-framework Docker images**: playwright, jest, pytest (last one exists)
8. **Browser-lane app runtime** — implement the health-poll loop:
   - Snapshotter parses `target.wait_for[].url` + `target.wait_for[].path`
   - Executor's docker-compose orchestrator runs `compose_file` services
   - Health-poll: HTTP HEAD against each `wait_for` URL every 2s for up to 120s
   - Failure mode: emit `status=executor_failed` with `phase=app_not_healthy`
     + the unreached URLs + the last response codes
   - Teardown: `docker compose down` after EACH subtask (or per-task per
     `test_data.isolation`)
9. **Evaluator per-language primitives**: tsc-preflight (TS),
   ESLint-lint (TS/JS), Stryker-mutate (TS/JS). Python primitives are
   already shipped in v0.1.
10. **Evaluator coverage adapter for non-Cobertura signals** — see
    Decision 11 below (coverage_delta is N/A for Browser lane; the Evaluator
    prompt is updated to treat null coverage as "not applicable", not "zero")
11. **Triager update-vs-create policy + catalog mutation** — uses the
    resolved AC-match algorithm from task 3
12. **Templates** — Playwright + Jest + pytest starter set (login-flow,
    form-submit, api-mocked, fixtures, etc.)
13. **Skills** — `pfactory-init`, `pfactory-add-test`, `pfactory-from-template`
    + the existing `handover-to-pfactory` skill updated for the new schema
14. **Portal** — new endpoint surface for templates / skills / catalogs;
    catalog browser + "which ACs aren't covered" view
15. **`LaneStatusGrid` reskin + new lane vocabulary** + the migration CLI
    helper `pfactory init` + `pfactory migrate v0_1_catalog`

---

## Appendix: full framework catalogue

(Reference — what's in scope for v0.4+ rollout)

### Unit / functional (xUnit family)

| Language | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|
| Python | pytest ✅, unittest | doctest | nose2 |
| JS/TS | Jest, Vitest, Mocha+Chai | Jasmine, Tape, AVA | node:test |
| Java | JUnit 5, JUnit 4, TestNG | Spock | JBehave |
| .NET | xUnit, NUnit | MSTest | Fixie |
| Go | testing + Testify | Ginkgo + Gomega | gocheck |
| Ruby | RSpec, Minitest | Test::Unit | |
| Rust | cargo test | proptest, quickcheck | |
| Kotlin | JUnit 5, Kotest | Spek | |
| Scala | ScalaTest | MUnit, Specs2 | uTest |
| PHP | PHPUnit | Pest, Codeception | |
| Swift | XCTest, Swift Testing | Quick + Nimble | |
| C/C++ | GoogleTest, Catch2 | Boost.Test, Doctest | CppUnit |
| Elixir | ExUnit | | |

### Browser E2E

Playwright, Cypress, Selenium WebDriver, WebdriverIO, Puppeteer, Appium, TestCafe

### API

Postman/Newman, REST Assured, Karate, supertest, Tavern, Pact, TestContainers, Bruno

### BDD

Cucumber, SpecFlow, Behave, Godog, Karate

### Mutation

mutmut, mutpy, Stryker (multi-target), PIT, mull

### Property-based / generative (functional strength)

Hypothesis, fast-check, jqwik, FsCheck, proptest, quickcheck, PropEr

### Mocking / assertion libraries (paired with unit frameworks)

Mockito, AssertJ, Hamcrest (Java) · Moq, NSubstitute, FluentAssertions (.NET) ·
Sinon, jest mocks, MSW (JS) · RSpec mocks (Ruby) · gomock, testify/mock (Go) ·
unittest.mock, pytest-mock, freezegun (Python)

---

## Spec metadata

- **Architectural decisions locked:** 12 (added Decision 12 — test evidence capture)
- **Frameworks catalogued:** ~80
- **Frameworks in v0.2:** 3 (Playwright, Jest, pytest)
- **Estimated v0.2 task count:** 16 (was 15 — added Task 16: evidence capture + portal viewer)
- **Predecessor spec:** PFactory v0.1.0-mvp walking-skeleton design
- **Reviewer round 1:** 3 blocking + 3 advisory issues found, all addressed
- **Late requirements folded in:** test evidence capture (screenshots + video + trace + HAR)
