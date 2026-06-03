---
name: pfactory-from-template
description: Pick one of the 15 v0.2 starter templates (5 each for pytest / Jest / Playwright), fill its vars, and drop the result into the project as a ready-to-run test file.
when_to_use:
  - Engineer wants a starter test from a canonical pattern (login-flow, function-pure, react-component, …)
  - "Give me the standard Playwright login-flow template"
  - User says "pfactory from template", "/pfactory-from-template", "generate a test from a template"
  - User wants deterministic output without invoking an LLM
allowed_tools:
  - Read
  - Write
  - Glob
  - Grep
  - Bash
---

# /pfactory-from-template

Render one of the 15 v0.2 starter templates (Task 12) into a real test file
in the current project. The skill is **LLM-free** — it just substitutes
`${var}` placeholders via Python's `string.Template`. It is the fastest way
to seed a project with a working test that exercises a known pattern.

> **What this skill does NOT do:** does not invoke Gen-Functional, does not
> run preflight_static, does not run the test. It writes a file. The
> templates themselves were verified to compile/lint clean at Task 12, so
> the output is parse-clean by construction.

## When to use

Trigger on:

- explicit `/pfactory-from-template`
- "use the login-flow template"
- "give me the pytest fixture-driven starter"
- "generate a react-component test"
- "scaffold a Playwright form-submit test"

Do NOT trigger when the user wants an LLM-generated test for a specific
symbol (use `/pfactory-add-test`) or a full pipeline run (use
`/handover-to-pfactory`).

## Procedure

### 1. Pick the framework

Ask the user (or detect from any file they named):

> Which framework?
>   1. **pytest**     — Python unit/integration tests
>   2. **jest**       — TypeScript/JavaScript unit + React component tests
>   3. **playwright** — End-to-end browser tests

Autoselect rules:
- If the user mentions `.py`, `.test.py`, "pytest", "Python" → `pytest`
- If the user mentions `.test.ts`, `.test.tsx`, "Jest", "React" → `jest`
- If the user mentions `.spec.ts`, "Playwright", "e2e", "browser", "login flow" → `playwright`

### 2. Show available templates

> **Two libraries, growing over time.** Beyond the built-in starter templates,
> the Triager **auto-harvests** high-confidence accepted tests (stable · mutation
> killed · high semantic relevance) into a reusable template library, so a test
> written once becomes a pattern future runs can start from. `load_templates_for_framework`
> reads all three sources (later shadows earlier on filename collision):
>
> 1. built-in — `frameworks/<fw>/templates/`
> 2. global — `~/.pfactory/templates/<fw>/` (harvested, cross-project)
> 3. project — `<project_dir>/.pfactory/templates/<fw>/` (harvested, committed
>    with the repo)
>
> Pass `project_dir=<checkout>` to include that project's harvested library.
> Each harvested template's frontmatter records `harvested_from`, `covers_acs`,
> and a `fingerprint` (dedup). Auto-harvest is on by default; control it with
> `PFACTORY_TRIAGER_HARVEST=0` (off) and `PFACTORY_TRIAGER_HARVEST_GLOBAL=1`
> (also write the global library).

Call:

```bash
PYTHONPATH=apps/backend python3 -c "
from templates_pkg.engine import load_templates_for_framework
import json
tmpls = load_templates_for_framework('${FRAMEWORK}', project_dir='${PROJECT_DIR}')
for name, t in tmpls.items():
    print(json.dumps({
        'name': name,
        'description': t.metadata.description,
        'requires_target': t.metadata.requires_target,
        'requires_auth': t.metadata.requires_auth,
        'vars': list(t.metadata.vars),
    }))
"
```

Present a numbered list, e.g. for `playwright`:

```
Available Playwright templates:
  1. login-flow.spec.ts.tmpl           — Standard login flow against a target with bearer auth
     requires_target=true   requires_auth=true   vars=[base_url, username, password, ...]
  2. form-submit-validation.spec.ts.tmpl — Form submission with client-side validation
     requires_target=true   requires_auth=false  vars=[base_url, form_selector, ...]
  3. api-mocked-flow.spec.ts.tmpl
  4. data-loaded-page.spec.ts.tmpl
  5. error-state.spec.ts.tmpl
```

### 3. Pick the template

Ask:

> Which template? (number or full name)

Validate the answer against the list. Reject silently? No — re-prompt.

### 4. Collect var values

For each var in the chosen template's metadata, prompt the user.
The template description in the front-matter is the source of truth; the
skill should NOT invent vars.

Example for `login-flow.spec.ts.tmpl`:

```
This template needs the following vars:
  - base_url         : the full https URL of the app (e.g. https://staging.example.com)
  - username         : test user (e.g. test_user@example.com)
  - password_env     : env-var NAME for the test password (e.g. TEST_USER_PASSWORD)
  - login_button_sel : Playwright selector for the login button
  - success_path     : URL path that means login worked (e.g. /dashboard)
```

> **Decision 7 again:** for `*_env` vars, collect the env-var NAME, not the
> value. Templates that take a literal password (without `_env`) suggest
> you should ask the user to pick a different template — flag it.

### 5. If `requires_target=true`, suggest reading `.pfactory.yml`

Templates with `requires_target: true` typically take a `base_url` that
should match a target in `.pfactory.yml`. Offer to read the file and
prefill the vars:

> Template requires a target. I see these in your `.pfactory.yml`:
>   - api  (http, base_url=https://api.staging.example.com)
>   - web  (http, base_url=https://staging.example.com)
> Use `web`? (y/n)

If the user picks one, prefill `base_url` from that target. If
`.pfactory.yml` is missing, just collect the var manually.

### 6. Render

Call:

```bash
PYTHONPATH=apps/backend python3 -c "
from pathlib import Path
from templates_pkg.engine import load_templates_for_framework

tmpls = load_templates_for_framework('${FRAMEWORK}')
t = tmpls['${TEMPLATE_NAME}']
body = t.instantiate(${KWARGS})
print(body)
" > /tmp/pfactory-rendered.txt
```

If `TemplateError` is raised (missing vars, unknown vars, unsubstituted
placeholders), surface the message — do not save a half-rendered file.

### 7. Pick the destination path

Suggest a default based on the framework's path conventions:

| Framework | Default destination |
|---|---|
| pytest | `tests/<test_name>.py` (strip `.tmpl` → `.py`) |
| jest | `__tests__/<test_name>.ts` (strip `.tmpl` → `.ts`/`.tsx`) |
| playwright | `tests/e2e/<test_name>.spec.ts` (strip `.tmpl` → `.spec.ts`) |

Convert the template filename: strip the trailing `.tmpl`, drop the
descriptive prefix (e.g. `login-flow.spec.ts.tmpl` → `login-flow.spec.ts`).

Ask:

> Save to `tests/e2e/login-flow.spec.ts`? (or paste a different path)

Honour their override. If the file already exists, prompt for overwrite.

### 8. Write + print next steps

After writing:

```
✓ Wrote tests/e2e/login-flow.spec.ts
  framework:  playwright
  template:   login-flow.spec.ts.tmpl
  vars:       base_url, username, password_env, login_button_sel, success_path

Run it locally:
  npx playwright test tests/e2e/login-flow.spec.ts

(Note: this skill did NOT run the test. The Playwright lane is the v0.2
Browser lane — run via the portal's Browser-lane Executor for an
isolated Docker run with evidence capture.)
```

## Failure modes

- **Framework not in registry** → tell the user to run
  `apps/backend/framework_registry/loader.py` and check
  `frameworks/<name>/descriptor.yaml` exists.
- **Template not found** → re-prompt with the available list.
- **Missing var** → `TemplateError.reason` lists the missing vars; ask for
  them.
- **Unknown var** → the user gave us a key the template doesn't expect;
  drop it and retry.
- **Destination file exists** → prompt for overwrite; do not silently
  clobber.
- **`PYTHONPATH` doesn't resolve `templates_pkg`** → tell the user to run
  this skill from inside a PFactory checkout or set `PYTHONPATH` to the
  PFactory `apps/backend` dir.

## Non-goals

- Does NOT use any LLM — pure substitution.
- Does NOT run the test or register it with the catalog.
- Does NOT validate that the rendered file passes preflight_static
  (templates were validated at Task 12 — they're parse-clean by
  construction, but you should still run the test before committing).
- Does NOT modify `.pfactory.yml` or `.pfactory/tests-catalog.json`.
