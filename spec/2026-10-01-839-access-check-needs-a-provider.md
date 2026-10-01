---
status: draft
issue: 839
intent: intent/2026-10-01-839-access-check-needs-a-provider.md
---

# Spec: suppress a cloud action only on positive evidence

## Design

`required_actions(plan)` (`plan/feasibility/access.py:48`) joins the plan's
title, description, every criterion and `raw_text`, then returns an IAM action
for every `_ACTION_HINTS` regex that matches. It has no notion of whether the
plan targets a cloud at all.

The fix adds a precondition, and the direction of its default is the whole
design decision:

> **Suppress a provider's actions only when there is positive evidence the plan
> does not target that provider. Never on the absence of evidence.**

Stated the other way round — "emit only when we are sure a cloud is targeted" —
the check would go quiet for any plan whose reconnaissance happened to be thin,
and a genuinely missing permission would stop being reported. That failure is
worse than today's noise: a false negative on access is an outage at handoff,
a false positive is a confusing low-severity line. So the gate stays noisy by
default and only shuts up when it has a reason.

The positive evidence is `RepoMap.deploy_system` (`plan/recon/models.py:59`,
populated by `recon/reconnoiter.py:162`), reachable as
`plan.repo_map.deploy_system`. A plan deploying by `kubectl` to an existing
cluster is not a plan creating an EKS cluster. Concretely:

```
_NON_CLOUD_DEPLOY_SYSTEMS = {"kubectl", "helm", "docker-compose", "none"}

emit provider P's actions unless:
    plan.repo_map is not None
    and plan.repo_map.available
    and (plan.repo_map.deploy_system or "").lower() in _NON_CLOUD_DEPLOY_SYSTEMS
    and no text in the plan names provider P explicitly
```

Every clause is a guard against suppressing wrongly: no repo map, an
unavailable one, an unrecognised deploy system, or any explicit mention of the
provider all keep the current behaviour. `kubernetes`/`k8s`/`postgres` are
**not** explicit mentions of AWS; `eks`, `rds`, `aws` are.

Note this fixes the reported case precisely. The session that prompted the issue
had `deploy_system: "kubectl"` in its own readiness output, and its text names
no AWS service — only "Kubernetes" and "postgres", which are exactly the generic
nouns the regexes over-read.

## Open questions, as resolved

Approval came without separate answers, so these follow the intent's
recommendations. Flagged so they can be overturned here.

1. **Which precondition: `deploy_system`.** It is concrete, already computed,
   and needs no new field or author action. An explicit target-environment
   declaration would be stronger and is more work; it can be layered later
   without undoing this.
2. **`relevant_providers` stays out of scope.** It shares the defect — same
   joined prose, same keyword matching — but `suggest_installs` is explicitly
   advisory and never blocking, so a wrong match there costs a suggestion, not
   a finding. Recorded in the intent so it is a known sibling rather than a
   future surprise. It should get the same precondition eventually.
3. **The `Create*`/use distinction is not built.** With the precondition in
   place the reported case emits nothing and the verb question never arises. It
   still matters for a plan that genuinely targets AWS and merely *reads* from
   an existing RDS instance; that is a separate, narrower issue.

## Alternatives rejected

- **A keyword-driven negation** ("provisions no cloud resources" in the text).
  Rejected outright: it has the same defect as the bug — prose deciding a
  machine question — and the intent's own evidence is that an author trying to
  comply with the remediation made the finding *worse*.
- **A recorded waiver, like `jurisdictions-declared`.** Legitimate, and still
  available for genuine exceptions, but it asks every non-cloud plan to declare
  a negative. The repo already states how it deploys.
- **Deleting the hints table.** Inferring access needs from a plan is worth
  doing; the problem is the missing precondition, not the inference.
- **Requiring a cloud provider to be named before emitting anything.** The
  inverted default, rejected above: it trades a visible false positive for a
  silent false negative.

## Risks

- **A genuinely-AWS plan in a repo whose `deploy_system` reads `kubectl`.**
  Suppression would hide a real requirement. Mitigated by the explicit-mention
  clause: such a plan almost certainly names EKS, RDS or AWS somewhere, and if
  it does, nothing is suppressed. This is the one way the change can do harm and
  the reason the condition is conjunctive.
- **`deploy_system` unpopulated when feasibility runs.** If `repo_map` is not
  attached yet at `plan/service.py:1501`, the guard never fires and the change
  is inert — a no-op, not a regression. Unverified today, so it is step 1 of
  verification rather than an assumption; the whole design rests on it.
- **No host risk.** The check is advisory (`low`, `soft`) and changes only which
  lines a readiness report contains.

## Verification

1. **Ordering, first, because the design depends on it.** Assert that
   `plan.repo_map` is populated at the point `assess_feasibility` is called —
   by test, not by reading. If it is not, the design changes (the precondition
   must move to the readiness check, which demonstrably has the deployment
   block) and this spec is revised before any code lands.
2. **The reported case, as a regression test.** A plan whose text contains
   "Kubernetes" and "postgres", no AWS term, and `deploy_system="kubectl"`
   yields **no** `aws:*` actions. Built from the real brief that prompted the
   issue.
3. **Each guard clause fails open, one test apiece:** `repo_map=None`,
   `available=False`, `deploy_system="terraform"`, and text naming `rds` — all
   still emit the actions they emit today.
4. **Mutations, one per clause.** Drop the explicit-mention clause and test (3)'s
   `rds` case must fail. Drop the `available` clause and its case must fail.
   Make the suppression unconditional and (3) must fail wholesale. A guard whose
   removal breaks nothing is not doing anything.
5. **No change to any non-suppressed path:** the existing `access.py` tests pass
   untouched, and the `azure`/`gcp` hints behave identically.
6. **Live:** re-ingest the brief from session
   `068-myfriends-web-remediation-v2-of-the-two-critical-f` and read
   `readiness.results[]` — `access-verified` must no longer list
   `aws:eks:CreateCluster, aws:ec2:RunInstances, aws:iam:CreateRole,
   aws:rds:CreateDBInstance`. That is the measurement the issue opened with, so
   it is the one that closes it.
7. **Gates:** ruff, `ruff format --check`, the ratchet with its `--package`
   flags, and the full suite.

## Rollback

Revert the PR. The hints table matches prose again and the four spurious actions
return. Nothing persists — `required_actions` is computed per plan, and stored
plans keep whatever they were stamped with either way.
