---
status: draft
issue: 797
---

# Intent: a hard gate's pass is indistinguishable from a stub returning pass

## Problem

`deployment-pipeline-present` (`apps/backend/plan/review/readiness/checks.py:529`)
is a hard, waivable gate: it governs whether a deployable change rides a usable
CI/CD pipeline. Its pass carries nothing:

    {"check_id": "deployment-pipeline-present", "status": "pass", "hard": true,
     "detail": "", "remediation": "", "citations": [], "evidence": {}}

Read in `checks.py`: `detail` and `evidence` are both built as
`… if needs else ""` / `{}`, so only the *failing* branch says anything. The
`not_applicable` branch does explain itself ("No deployment dimension — nothing to
ship"), which makes a pass the least informative of the three outcomes.

The information exists and is already derived. The deployment block that the check
reads carries, for the run in #797:

    ci_system: github-actions, ci_exists: true,
    ci_pipeline_paths: [".github/workflows/kotlin-core.yml"], needs_pipeline: false

Verified those are real fields on the block (`plan/feasibility/deployment.py:290`).
So a pass could say "github-actions pipeline found at
.github/workflows/kotlin-core.yml" and a reviewer reading the audit pack could
check it. As shipped, the pass of a gate that governs whether a change may ship is
byte-identical to what a stub returning `pass` would produce.

Sibling checks already do the informative thing on their pass paths —
`language-reconciled` (line 152) and `constitution-grounded` (line 238) both attach
evidence unconditionally. So this is consistency, not a new idea.

## Measured: the pattern is the file's norm, not this check's quirk

I walked every `ReadinessCheckResult` literal in `checks.py` that can produce a
pass, and checked whether `detail` and `evidence` are both empty on that branch.
**14 checks qualify, and 12 of them are hard gates:**

    children-present · criteria-present · ac-child-coverage ·
    service-requirements-covered · deps-sound · access-granted · env-buildable ·
    deployment-pipeline-present · enrichment-integrity · no-blocking-findings ·
    decompose-trustworthy · criteria-self-consistent        (hard)
    ac-testable · access-verified                           (not hard)

That reframes the issue: an evidence-free pass is how this file mostly behaves, so
#797 is one instance of a systemic shape rather than a one-off defect.

## Desired outcome

`deployment-pipeline-present`'s pass states what it found — the CI system and the
pipeline path — in `detail` and in `evidence`, so a human reading the audit pack
can check the gate rather than trust it. No change to when it passes or fails.

## Affected

- `apps/backend/plan/review/readiness/checks.py` — the pass branch of one check.
- Its tests, and any audit-pack/contract test that asserts on this check's shape.

## Constraints

- **The verdict must not move.** This is presentation of an existing decision; a
  brief that fails today must still fail, and one that passes must still pass.
- Evidence goes into the audit pack, so it must contain no secrets — `ci_system`
  and repo-relative workflow paths are safe; nothing here should start carrying
  tokens or URLs with credentials.
- `detail` is human-facing prose in the portal; keep it one sentence.

## Open questions

1. **Scope: this check, or all 12 hard gates?** My recommendation is **this check
   only**, and file the systemic finding as its own issue. Reasons: #797 names one
   check and one observed run; the data for that check is already derived, whereas
   the other 11 would each need their own judgement about what is worth attesting;
   and a 12-gate sweep of the review contract is a design change that deserves its
   own intent rather than riding a bug fix. Say if you would rather do the sweep.
2. **Should an evidence-free pass on a hard gate become a test-enforced rule?**
   That is the durable fix for the class — a test asserting every hard check's pass
   carries at least one of `detail`/`evidence`. It belongs with whatever answer
   question 1 gets, because it would fail for 11 checks on day one.
3. **Does `remediation` stay empty on a pass?** I think yes — there is nothing to
   remediate — but the issue quotes it alongside the empty fields, so worth
   confirming that only `detail` and `evidence` are in scope.
