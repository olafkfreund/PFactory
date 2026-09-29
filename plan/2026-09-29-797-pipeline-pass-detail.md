---
status: draft
issue: 797
spec: spec/2026-09-29-797-pipeline-pass-detail.md
---

# Plan: A hard gate's pass says nothing a reviewer can check

Approved decisions (from the spec):

- Only the pass path of `deployment-pipeline-present` changes
  (`apps/backend/plan/review/readiness/checks.py:529`). Status, severity,
  `hard`, `waivable`, `remediation` and the whole failing branch are untouched.
  No `citations` on a pass (intent Q1).
- `detail` is assembled from facts the block holds, because `has_deploy` cannot
  be reconstructed (`deploy_manifests` is not in the block):
  - `ci_exists` true → `"<ci_system> pipeline found: <paths joined by ', '>"`,
    and the path clause is omitted entirely when `ci_pipeline_paths` is empty or
    absent;
  - `ci_exists` false → `"No CI pipeline detected, and this change needs none
    (needs_pipeline=false)."` — must not contain "found";
  - `deploy_system` appended as `" Deploy system: <x>."` only when set and not
    `"none"`.
- `evidence` on pass carries `ci_system`, `ci_exists`, `ci_pipeline_paths`,
  `deploy_system`, `risk_class` — the same keys a reader gets on a fail, plus
  the pipeline facts. Absent keys report what the block holds, not invented
  values.

## Steps

1. `apps/backend/plan/review/readiness/checks.py`: add a small module-level
   helper that builds the pass detail from the block (kept out of the result
   literal so the two cases are readable), and fill `detail` / `evidence` on the
   pass branch. → verify by step 2's tests.
2. `tests/test_deployment_aware_planning.py` (reusing `_epic()`, `_plan()`,
   `_REGISTRY`), asserting on the returned result:
   a. `ci_exists` true, two paths → detail names the system and both paths;
      evidence has all five keys;
   b. `ci_exists` true, `ci_pipeline_paths: []` → detail names the system, and
      contains neither `[]` nor a trailing colon;
   c. `ci_exists` false, `needs_pipeline` false → detail says none detected and
      none needed, and `"found" not in detail`;
   d. `deploy_system: "none"` → `"none" not in detail`;
   e. `deploy_system: "helm"` → detail mentions helm.
   → verify green.
3. Confirm the existing three tests (fail / not_applicable / AC injection) still
   pass unchanged.
4. Negative control (not committed): set the pass `detail` back to `""` → cases
   a-c fail. Restore.
5. Checks: `pytest tests/test_deployment_aware_planning.py -q`, then
   `pytest tests/ -q -k "readiness or deployment"`; ruff on the changed file;
   `mypy --strict` on it compared with `dev` (run locally — the hook's ratchet
   is ruff-only, #786).

## Tests

    apps/backend/.venv/bin/pytest tests/test_deployment_aware_planning.py -q
    apps/backend/.venv/bin/pytest tests/ -q -k "readiness or deployment"
    apps/backend/.venv/bin/python -m mypy --config-file standards/mypy.ini \
      --explicit-package-bases --namespace-packages \
      apps/backend/plan/review/readiness/checks.py

Expected: all pass; no new mypy errors vs `dev`; full suite via the hook.

## Rollback

Revert the commit; the pass returns to an empty detail and evidence. No schema,
no state, no behaviour change to gate outcomes either way.
