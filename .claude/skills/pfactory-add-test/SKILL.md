---
name: pfactory-add-test
description: Add ONE generated test to an existing project by running the Gen-Functional sub-flow locally, without the full Planner → Executor → Evaluator → Triager pipeline.
when_to_use:
  - Engineer wants a single test for a specific symbol/file without handing off a whole branch
  - Quick "give me a starting test for this function" requests
  - User says "pfactory add test", "/pfactory-add-test", "generate a test for X"
  - User has already run /pfactory-init at least once for this repo
allowed_tools:
  - Read
  - Write
  - Glob
  - Grep
  - Bash
---

# /pfactory-add-test

Generate ONE test file via Gen-Functional, without firing the full PFactory
pipeline. The skill builds a minimal one-element `Subtask` matching the
v0.2 schema (`language`, `framework`, `target_name`, `intent`,
`files_to_create`), drops it into a throwaway `test_plan.json`, and runs
`run_gen_functional` against it.

> **What this skill does NOT do:** does not run the Executor (no Docker),
> does not run the Evaluator (no verdicts), does not run the Triager (no
> dedup, no PR comment), does not register with the portal, does not
> commit to git. Output is a single test file on disk.

## When to use

Trigger on:

- explicit `/pfactory-add-test`
- "add a test for `foo.py::bar`"
- "generate a single test for the login endpoint"
- "write a quick test for this function"

Do NOT trigger when the user wants a template-driven scaffold (use
`pfactory-from-template`) or a full pipeline run (use `handover-to-pfactory`).

## Procedure

### 1. Confirm the repo is initialised

Verify `.pfactory.yml` exists at cwd. If not:

> No `.pfactory.yml` found at the repo root. Run `/pfactory-init` first so
> PFactory knows about your targets, then re-run `/pfactory-add-test`.

Stop. Do not synthesise a config on the fly.

### 2. Collect the test target

Ask the user:

> Which file + symbol does this test exercise? Format:
> `<repo-relative path>::<symbol>` — e.g. `app/auth/login.py::login_user`
> or `src/api/users.ts::createUser`.

Parse the answer. Validate that the path part exists. If it doesn't,
re-prompt — do not fabricate paths.

### 3. Collect the acceptance criterion rationale

Ask:

> Which acceptance criterion does this test cover? Paste the AC line
> verbatim (e.g. "AC#3: User receives 401 when token is expired").

Store the answer as `rationale`. This is what the Planner would have
normally written and is the human-readable provenance for the test.

### 4. Detect language

From the file extension:

| Extension | language |
|---|---|
| `.py` | `"python"` |
| `.ts`, `.tsx` | `"typescript"` |
| `.js`, `.jsx` | `"typescript"` (Jest covers both) |
| `.go` | `"go"` (future) |
| `.rs` | `"rust"` (future) |

If the language is anything other than `python` or `typescript`, warn
the user that only the v0.2 spine (pytest / Jest / Playwright) is wired
and stop.

### 5. Pick the framework

Look up the framework registry to confirm which frameworks are available
for the detected language:

```bash
PYTHONPATH=apps/backend python3 -c "
from framework_registry import load_registry
reg = load_registry()
for name, desc in reg.items():
    print(f'{name}: lanes={desc.lanes}, language={desc.language}')
"
```

Default mapping for a single-file generation:

| Language | Default framework | When to override |
|---|---|---|
| `python` | `pytest` | (none — pytest only at v0.2) |
| `typescript` | `jest` | Ask the user if it's a UI flow → `playwright` |

If the user said the test is for "a login flow" / "a form submission" /
"an end-to-end browser flow", offer `playwright` instead of `jest`.

### 6. Build the minimal `Subtask` dict

Construct an in-memory subtask matching the v0.2 schema
(`apps/backend/test_plan/subtask.py`):

```python
subtask = {
    "id": "ad-hoc-1",
    "description": f"Generate test for {target}",
    "status": "pending",
    "lane": "unit",                # or "browser" / "api" depending on framework
    "target": target,              # "<path>::<symbol>"
    "rationale": rationale,
    "language": language,          # "python" | "typescript"
    "framework": framework,        # "pytest" | "jest" | "playwright"
    "target_name": None,           # not bound to a .pfactory.yml target
    "intent": "create",
    "files_to_create": [<derived test path>],
    "files_to_modify": [],
    "patterns_from": [],
}
```

Derive the test path from the framework descriptor's
`test_path_conventions`. For pytest, that's typically
`tests/test_<module>.py`. For Jest, `__tests__/<module>.test.ts`. For
Playwright, `tests/e2e/<feature>.spec.ts`.

### 7. Drop into a scratch spec dir

Pick a scratch spec dir under the workspace root:

```
$PFACTORY_WORKSPACE_ROOT/specs/_adhoc_<UTC-timestamp>/
```

(default `~/.pfactory/workspaces/<project>/specs/_adhoc_…/`).

Write:

- `context/aifactory_spec.md` — a one-line stub with the rationale
- `context/diff.patch` — empty file
- `context/source.json` — `{"branch": <current>, "base_ref": "main", "repo": <cwd basename>}`
- `test_plan.json` — `{"version": 1, "subtasks": [<the subtask above>]}`
- `status.json` — `{"status": "planned", "phase": "planner_complete"}`

### 8. Run Gen-Functional locally

Invoke the agent's public entry point:

```bash
PYTHONPATH=apps/backend python3 -c "
import asyncio
from pathlib import Path
from agents.gen_functional import run_gen_functional

result = asyncio.run(run_gen_functional(
    spec_dir=Path('$SCRATCH_SPEC_DIR'),
    project_dir=Path('$REPO_ROOT'),
    mode='initial',
    verbose=False,
))
print(f'gen_functional result: {result}')
"
```

The agent will write the test file to the path you specified in
`files_to_create`, run preflight_static + flake_risk_lint, and mark the
subtask `generated` (or `replan_needed` on guardrail rejection).

### 9. Show the result

If `result is True`:

```
✓ Test generated:        <repo-relative path of the file>
  framework:             <pytest|jest|playwright>
  covers AC:             "<rationale>"
  preflight + flake-lint: passed

Run it locally:
  pytest <path>         # python
  npm test -- <path>    # jest
  npx playwright test <path>   # playwright
```

If `result is False` (guardrails rejected):

- Read `<scratch_spec_dir>/context/replan_request.json` (Gen-Functional
  writes it on rejection)
- Read `<scratch_spec_dir>/logs/gen_functional.log` for detail
- Tell the user which guardrail rejected (preflight_static or
  flake_risk_lint) and the reason. Suggest:
  > The generated test failed a guardrail. Try `/pfactory-from-template`
  > with a known-good template, or re-run with a sharper rationale.

### 10. Catalog update is OUT OF SCOPE

This skill writes a test file but does NOT append a `CatalogEntry` to
`.pfactory/tests-catalog.json`. The catalog is the Triager's
responsibility (Task 11). Engineers who want catalog tracking should run
the full pipeline via `/handover-to-pfactory`.

## Failure modes

- **`.pfactory.yml` missing** → tell the user to run `/pfactory-init` first.
- **Target path doesn't exist** → re-prompt; do not fabricate.
- **Unsupported language** → only python + typescript are wired at v0.2;
  warn and stop.
- **Gen-Functional rejects** → surface the replan_request.json reason; do
  not retry silently. Suggest the template-based skill as a workaround.
- **No SDK credentials** → Gen-Functional needs `ANTHROPIC_API_KEY` (or the
  provider env vars from `phase_config`). Tell the user to set them.
- **Permission denied writing the test file** → tell the user; do not
  retry with sudo.

## Non-goals

- Does NOT run the Executor, Evaluator, or Triager.
- Does NOT write to `tests-catalog.json` (Triager owns it).
- Does NOT commit to git or push.
- Does NOT register the project with the portal.
- Does NOT support more than one subtask per invocation — use
  `/handover-to-pfactory` for multi-test plans.
