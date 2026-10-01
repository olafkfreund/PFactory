---
status: approved
issue: 839
author: olafkfreund
---

# Intent: a cloud IAM action is only required if the plan targets that cloud

## Problem

The `access-verified` readiness check reports cloud IAM actions a plan needs,
inferred from nouns in its prose. A plan that provisions nothing outside the
existing k3d cluster was reported as needing four AWS actions:

```
FAIL access-verified | low soft
Unverified actions: aws:eks:CreateCluster, aws:ec2:RunInstances,
                    aws:iam:CreateRole, aws:rds:CreateDBInstance
```

Session `068-myfriends-web-remediation-v2-of-the-two-critical-f`. The plan adds
authorisation checks to a FastAPI service and moves four in-memory dicts into
the Postgres instance already running in the cluster.

`apps/backend/plan/feasibility/access.py:25`:

```python
(re.compile(r"(?i)\beks\b|kubernetes|k8s"), "aws", ["eks:CreateCluster", "ec2:RunInstances", "iam:CreateRole"]),
(re.compile(r"(?i)\brds\b|postgres|aurora"), "aws", ["rds:CreateDBInstance"]),
```

`required_actions` joins the title, description, every criterion and `raw_text`,
then reports a match. So "postgres" implies a managed database instance and
"kubernetes" implies a cluster plus instances plus a role — regardless of
provider, of whether the plan provisions anything, or of whether any cloud
account is in scope.

Nothing corroborated the finding on the cluster it ran against:

- `access_approvals` was `{}` and `access_audit` was `[]` — nothing requested.
- No AWS credential secret exists in the `factory` namespace (azure and gcp
  demo creds do; AWS does not).
- No `AWS_*` or role-ARN variable in the pfactory pod's environment.

## The part that makes it actively misleading

The first ingest of this brief reported **one** action
(`rds:CreateDBInstance`). The brief was then edited specifically to rule
provisioning out, adding:

> deployed into the existing Kubernetes cluster ... **no new infrastructure is
> provisioned** by this work ... Nothing here creates a managed database
> instance, an RDS instance, or any other cloud resource; there is no cloud
> provider in this path at all.

After that edit the finding reported **four**. Saying "Kubernetes" in order to
deny it added `eks:CreateCluster`, `ec2:RunInstances` and `iam:CreateRole`. An
author who reads the remediation text and tries to comply makes the finding
worse. The check cannot be negated by the text it reads, which is the opposite
of what a gate should do.

## Proposed outcome

- No `aws:*`, `azure:*` or `gcp:*` action is reported for a plan that does not
  target that cloud.
- A plan can state that it provisions no cloud resources and be believed, the
  way `jurisdictions-declared` already accepts a recorded waiver.
- Using a service is distinguished from creating one: `postgres` as a client
  dependency is not `rds:CreateDBInstance`. Every action in the table is a
  `Create*`, but the regex matches the noun, not a creation verb.
- The check keeps its value. Inferring access needs from a plan is worth doing;
  the problem is that the inference has no precondition.

## Affected users and systems

- `apps/backend/plan/feasibility/access.py` — `_ACTION_HINTS` and
  `required_actions`.
- Whatever renders `access-verified` into `readiness.results[]`.
- **A signal that already exists and would serve as the precondition:**
  `RepoMap.deploy_system`, which `feasibility/deployment.py:248` already reads
  and which the readiness report for this very plan printed as
  `"deploy_system": "kubectl"`. A plan deploying by kubectl to an existing
  cluster is not a plan creating an EKS cluster.
- **A sibling with the same shape**, worth deciding about rather than
  discovering later: `plan/providers/registry.py:106` `relevant_providers`
  matches provider relevance keywords against the same joined prose. It is
  less harmful — `suggest_installs` is explicitly advisory and never blocking —
  but it is the same inference from the same text.

## Constraints

- **Must not** stop reporting genuinely required access. A plan that really does
  provision RDS must still say so; this is about plans that do not.
- **Must not** become a blanket opt-out. The `jurisdictions-declared` precedent
  is a *recorded* waiver, not a magic phrase, and any negation here should be
  explicit and auditable rather than keyword-driven — otherwise the fix has the
  same defect as the bug.
- The finding is currently `low` severity and `soft`, so it blocks nothing.
  That is why this is a correctness issue rather than an outage, and it should
  not be used as a reason to leave it: a soft finding that is wrong teaches
  people to ignore the gate.

## Open questions

1. **Which precondition?** `deploy_system` is concrete and already computed,
   but a plan can target a cloud without naming a deploy system. An explicit
   target-environment field would be stronger and is more work. Which?
2. **Scope: the sibling.** Fix `access.py` only, or `relevant_providers` too?
   They share the defect; only one of them is reported as a finding.
3. **Is the `Create*` distinction worth building, or does the precondition make
   it unnecessary?** If no cloud is targeted, nothing is reported at all and the
   verb question never arises. It would still matter for a plan that genuinely
   targets AWS and merely *reads* from an existing RDS instance.

## Related

- The `.trivyignore` and `jurisdictions-declared` waiver patterns, as the
  precedent for how a deliberate exception is recorded in this codebase.

Found driving olafkfreund/pfactory-friends-demo#113 through the factory.
