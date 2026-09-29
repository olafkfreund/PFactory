---
status: approved
issue: 797
intent: intent/2026-09-29-797-pipeline-pass-detail.md
---

# Spec: A hard gate's pass says nothing a reviewer can check

## Facts established

- The deployment block (`plan/feasibility/deployment.py:291`) carries
  `ci_system`, `ci_exists`, `ci_pipeline_paths`, `needs_pipeline`,
  `deploy_system`, `risk_class` and more. The check already reads this block.
- **`has_deploy` cannot be reconstructed from the block.** It is
  `deploy_system not in ("none",) or bool(rm.deploy_manifests)` (`:267`), and
  `deploy_manifests` is not in the block. So the check must not claim anything
  about whether the change is deployable beyond what `deploy_system` and
  `ci_exists` actually say.
- `language-reconciled` (`checks.py:145`) is the house pattern for a pass:
  a short human `detail` plus an `evidence` dict of the values behind it.
- Decided (intent Q1): no `citations` on the pass path.

## Design

One change, in the `deployment-pipeline-present` pass/fail return
(`checks.py:529`). Status, severity, `hard`, `waivable` and the whole failing
branch are untouched.

**`detail` on pass** is assembled from the facts present, not from a narrative:

- `ci_exists` true ⇒
  `"github-actions pipeline found: .github/workflows/kotlin-core.yml"` —
  the `ci_system` value and the `ci_pipeline_paths` joined by `", "`.
  When `ci_pipeline_paths` is empty or absent, the path clause is omitted
  entirely: `"github-actions pipeline found"` (intent constraint — an absent
  list must not read as an empty pipeline list).
- `ci_exists` false ⇒
  `"No CI pipeline detected, and this change needs none (needs_pipeline=false)."`
  This is the case the intent warned about: saying "pipeline found" here would
  be false.
- `deploy_system` is appended when it is set and not `"none"`:
  `" Deploy system: helm."` It is left out otherwise rather than printed as
  `none`.

**`evidence` on pass** mirrors the failing branch's shape plus the pipeline
facts, so a reader gets the same keys whichever way the gate went:

    {"ci_system": ..., "ci_exists": ..., "ci_pipeline_paths": [...],
     "deploy_system": ..., "risk_class": ...}

Absent keys are reported as whatever the block holds (`None` / `[]`) rather
than invented.

`remediation` stays empty on pass — there is nothing to remediate.

## Alternatives rejected

- **Reconstruct `has_deploy`** to say "a deployable surface rides an existing
  pipeline": the block does not carry `deploy_manifests`, so this would be a
  guess dressed as a finding — the defect this issue is about.
- **One detail string for both pass cases**: would state a pipeline was found in
  the case where none was looked for.
- **Add `citations`** (intent Q1): decoration on a pass.
- **Put the block in `evidence` wholesale**: the block carries a dozen keys
  (dora_context, system_gates, verification); dumping it is not the same as
  reporting what the check examined.

## Risks

- Anything asserting on the empty pass `detail`/`evidence` would break. Checked:
  `tests/test_deployment_aware_planning.py` is the only test referencing this
  check; the spec's verification includes running it.
- The audit pack gains a few lines per run. That is the point.

## Verification

- New cases in `tests/test_deployment_aware_planning.py`, asserting the result
  the gate returns rather than reading the block back:
  - `ci_exists` true with two paths ⇒ detail names the system and both paths;
    evidence carries all five keys.
  - `ci_exists` true with `ci_pipeline_paths: []` ⇒ detail names the system and
    says nothing about paths (no empty list, no stray colon).
  - `ci_exists` false, `needs_pipeline` false ⇒ detail says no pipeline was
    detected and none is needed, and does NOT contain "found".
  - `deploy_system: "none"` ⇒ the word `none` does not appear in the detail.
  - The failing branch's detail/evidence are unchanged (existing assertions).
- Negative control: revert the pass-branch detail to `""` ⇒ the new cases fail.
- `pytest tests/test_deployment_aware_planning.py tests/ -k readiness -q`.
