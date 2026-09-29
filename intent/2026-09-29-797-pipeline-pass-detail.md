---
status: approved
issue: 797
author: Olaf Krasicki-Freund
---

# Intent: A hard gate's pass says nothing a reviewer can check

## Problem

`deployment-pipeline-present` (`apps/backend/plan/review/readiness/checks.py:506`)
is a hard, waivable gate. Its passing result carries nothing:

```json
{"check_id": "deployment-pipeline-present", "status": "pass", "hard": true,
 "detail": "", "remediation": "", "citations": [], "evidence": {}}
```

`detail` and `evidence` are filled only on the failing branch. The
`not_applicable` branch explains itself ("No deployment dimension — nothing to
ship."), so a pass is the least informative of the three outcomes — and a pass
with no detail is indistinguishable from a stub that returns pass, on a gate
that governs whether a change may ship.

The information is already derived and sitting in the same block the check
reads (`plan/feasibility/deployment.py:291`): `ci_system`, `ci_exists`,
`ci_pipeline_paths`, `needs_pipeline`, `deploy_system`, `risk_class`. For the
run in #797 that was `github-actions` with
`.github/workflows/kotlin-core.yml`.

Sibling checks already do this on their pass path — `language-reconciled`
returns a human `detail` plus an `evidence` dict — so this is consistency, not
a new convention.

## Proposed outcome

- A passing `deployment-pipeline-present` names what it found: the CI system and
  the pipeline path(s), with the same values in `evidence`, so the audit pack is
  checkable by a human.
- The two distinct reasons a pass happens are not conflated: "a deployable
  surface rides an existing pipeline" and "there is no deployable surface in
  this change" must read differently. A pass that claimed a pipeline was found
  when none was looked for would be the same defect wearing new text.
- No change to any status, severity, hardness or waivability — only to what a
  result says about itself.

## Affected users and systems

- Anyone reading a readiness report or audit pack, and the review gate's own
  credibility.
- `apps/backend/plan/review/readiness/checks.py` (one check), and
  `tests/test_deployment_aware_planning.py`.

## Constraints

- Derived values only: the check must not re-derive or re-probe anything, just
  report what the deployment block already holds.
- A missing key must not produce a confident sentence — an absent
  `ci_pipeline_paths` should read as absent, not as an empty pipeline list.
- The failing branch stays exactly as it is (it is already informative).

## Open questions

1. Should the pass path also carry `citations` (the RFC-0013 reference the
   failing path cites), or is detail + evidence enough? Recommendation: detail +
   evidence only — citations on a pass would be decoration, and the issue asks
   for auditability, not provenance.
