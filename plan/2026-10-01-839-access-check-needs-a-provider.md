---
status: approved
issue: 839
spec: spec/2026-10-01-839-access-check-needs-a-provider.md
---

# Plan: suppress a cloud action only on positive evidence

## Approved decisions (self-contained)

- **Why.** `plan/feasibility/access.py:25` matches nouns in a plan's prose:
  "kubernetes" implies `eks:CreateCluster` + `ec2:RunInstances` +
  `iam:CreateRole`, "postgres" implies `rds:CreateDBInstance`. Session
  `068-...` reported four AWS actions for a plan that provisions nothing, in a
  namespace with no AWS credential at all. Editing the brief to *deny*
  provisioning raised the count from one to four, because the denial had to name
  the thing the regex matches.
- **The design decision is the direction of the default.** Suppress a provider's
  actions only on positive evidence the plan does **not** target it — never on
  absence of evidence. Inverted, the check goes quiet whenever reconnaissance is
  thin, turning a visible false positive into a silent false negative; a missed
  permission is an outage at handoff, a spurious `low`/`soft` line is only
  confusing.
- **The evidence is `RepoMap.deploy_system`** (`plan/recon/models.py:59`,
  populated by `recon/reconnoiter.py:162`), reachable as
  `plan.repo_map.deploy_system`, and already `"kubectl"` for the session that
  prompted the issue.
- **The condition is conjunctive, every clause a guard against suppressing
  wrongly:** no repo map, `available=False`, an unrecognised deploy system, or
  any explicit mention of that provider all keep today's behaviour.
  `kubernetes`/`k8s`/`postgres` are **not** explicit mentions of AWS; `eks`,
  `rds`, `aws` are.
- **Rejected: a keyword-driven negation.** It has the same defect as the bug —
  prose deciding a machine question — and the evidence is that an author trying
  to comply made the finding worse. A recorded waiver stays available for genuine
  exceptions.
- **Out of scope, recorded:** `plan/providers/registry.py:106`
  `relevant_providers` shares the defect but is advisory and never blocking.
  `Create*`-vs-use is unnecessary once the precondition exists for this case.

## Steps

Branch `fix/839-access-check-needs-a-provider` off `dev` (already created).

1. **Settle the ordering question first — the whole design rests on it.** A test
   asserting `plan.repo_map` is populated at the point `assess_feasibility` is
   called (`plan/service.py:1501`). I could not establish this by reading, and
   inferring execution order from file position is exactly the mistake to avoid.
   → verify: if it is populated, continue. **If it is not, stop and revise the
   spec**: the precondition moves to the readiness check, which demonstrably has
   the deployment block, and no code lands under this plan until that is
   re-approved. A `deploy_system` that is never populated here would make the
   guard inert — a silent no-op that looks like a fix.
2. **`_NON_CLOUD_DEPLOY_SYSTEMS`** and an explicit-mention helper in
   `access.py`: the set `{"kubectl","helm","docker-compose","none"}`, and a
   per-provider term list (`aws` → `aws`, `eks`, `rds`, `s3`, `elasticache`,
   `ec2`, `iam`) distinct from the generic nouns in `_ACTION_HINTS`.
   → verify: unit tests on the helper alone — `"postgres"` is not an AWS
   mention, `"rds"` is.
3. **The precondition in `required_actions`**, suppressing a provider's actions
   only when all clauses hold.
   → verify: step 4.
4. **The reported case, as a regression test**, built from the real brief: text
   containing "Kubernetes" and "postgres", no AWS term,
   `deploy_system="kubectl"` → **no** `aws:*` actions.
5. **Fail-open tests, one per clause:** `repo_map=None`, `available=False`,
   `deploy_system="terraform"`, and text naming `rds` — each still emits what it
   emits today.
   **Mutations, one per clause:** drop the explicit-mention clause and the `rds`
   case must fail; drop the `available` clause and its case must fail; make the
   suppression unconditional and step 5 must fail wholesale. A guard whose
   removal breaks nothing is not doing anything.
6. **No collateral change:** the existing `access.py` tests pass untouched, and
   the `azure`/`gcp` hints behave identically.
7. **Live (amended 2026-10-02, approved).** Re-ingest the brief with its one
   AWS term removed from the denial, and read `readiness.results[]`:
   `access-verified` must no longer list `aws:eks:CreateCluster,
   aws:ec2:RunInstances, aws:iam:CreateRole, aws:rds:CreateDBInstance`.

   The original wording said to re-ingest the brief **as-is** and expect that.
   It cannot: the brief contains "RDS" inside the sentence denying it, so the
   mention clause correctly declines to suppress. Measured both ways —
   as-written yields 4 AWS actions, the same brief with that one word removed
   yields 0.

   So the live proof is run on the corrected brief, and the as-written case is
   recorded as **expected** behaviour rather than a miss: a gate that cannot
   read negation must treat a named provider as named.
8. **PR → `dev`** linking intent, spec and plan; close #839. File
   `relevant_providers` as its own issue so the sibling is tracked rather than
   rediscovered.

## Step 1 result (2026-10-02): PASSES — the plan stands

`plan.repo_map` **is** populated when `assess_feasibility` runs. Confirmed two
independent ways rather than by reading file order:

**Statement order inside one function body**, which is real execution order:

```python
# plan/service.py, process()
plan, descriptor = self._detect_and_plan_type(session)   # 1190 -> _reconnoiter -> sets repo_map
epic = self._decompose(session, plan, descriptor, llm=llm)
artifacts = synthesize(plan, epic, descriptor=descriptor)
composed_runner = self._build_review_runner(...)         # 1194 -> assess_feasibility (1501)
```

`_reconnoiter` is reached from `_detect_and_plan_type` (service.py:1415) and
sets `{"repo_map": repo_map}` at service.py:1553.

**The live session that produced the bad finding.** Session
`068-myfriends-web-remediation-v2-of-the-two-critical-f`:

```
plan.repo_map present: True | available: True | deploy_system: kubectl
epic.access_requirements present: True
```

So the exact signal the design depends on was present, with the exact value the
design keys on, in the exact run that reported four spurious AWS actions. The
guard would have fired.

Steps 2-8 proceed as written.

## Step 3 measured: correct, but step 7's expectation is wrong

The precondition works. Measured against the real brief from session
`068-...`, with `deploy_system="kubectl"`:

| input | aws actions |
| --- | --- |
| the brief as written | **4** — the guard declines to suppress |
| the same brief, one word removed | **0** — the guard fires |

The word is `RDS`. Line 51 of `specs/myfriends-remediation.md` reads:

> ... Nothing here creates a managed database instance, an **RDS** instance, or
> any other cloud resource; there is no cloud provider in this path at all.

It is the only AWS term anywhere in the brief, and it appears **inside the
sentence denying it**. So `_mentions_provider` sees an explicit AWS mention and
correctly keeps today's behaviour, exactly as the spec specifies.

This is the issue's own complaint turned on the fix: "an author who reads the
remediation text and tries to comply makes the finding worse". My brief named
RDS in order to deny RDS, and the guard cannot tell a denial from a plan.

**The design is unchanged and still right.** A plan whose text names RDS is
genuinely ambiguous as text, and the governing rule is to suppress only on
positive evidence — a mention, even a negated one, is not that evidence. The
alternative is parsing negation out of prose, which the spec rejects on the
grounds that prose deciding a machine question is the defect itself.

**What is wrong is step 7**, which says to re-ingest that brief and expect no
AWS actions. As written that expectation cannot hold. Steps 4 and 5 are
unaffected — step 4 already specifies "no AWS term" — so implementation
continues; step 7 needs the approver's call between:

- **(a)** re-point step 7 at a brief that names no provider, and record that a
  denial naming a provider legitimately keeps the gate noisy (recommended —
  smallest change, and the design is sound);
- **(b)** drop the explicit-mention clause and rely on `deploy_system` alone,
  re-opening the one harm the spec identified;
- **(c)** handle negation, which the spec rejects.

**Approved 2026-10-02: (a).** Step 7 below is amended accordingly, and the
behaviour is recorded as correct rather than worked around: a plan naming a
provider — even inside a denial — keeps its actions, because the gate cannot
distinguish a denial from a plan and must not guess. The brief is also corrected
separately, since naming providers in a negation is a bad habit independent of
this gate: "or any other cloud resource" says the same thing without tripping a
keyword match.

Separately worth doing either way: the brief should not name providers in a
denial. "or any other cloud resource" says the same thing without tripping a
keyword gate.

## Step 6: the guard's reach, measured exactly

Worth recording because it shows the change does precisely what it should and
nothing more. For every word in `_ACTION_HINTS`, is it also an explicit mention
of the provider it triggers?

| hint word | provider | explicit mention of it | suppressible |
| --- | --- | --- | --- |
| `kubernetes`, `postgres`, `bucket`, `redis` | aws | **no** | **yes** |
| `eks`, `rds`, `s3`, `elasticache` | aws | yes | no |
| `aks` | azure | yes | no |
| `gke` | gcp | yes | no |

So the guard can only ever suppress when the **sole** trigger is a generic noun
— which is exactly the reported case ("Kubernetes" and "postgres", no AWS term).
A plan naming any provider-specific service keeps its actions.

For azure and gcp the hint pattern is a *subset* of the mention pattern
(`\baks\b` ⊆ `\baks\b|\bazure\b`), so suppression can never apply to them
at all. That makes those two entries inert **today** — but not before step 2b:
with no azure entry, `_mentions_provider` returned `False` and a plan naming
"AKS" with `deploy_system="kubectl"` *was* suppressible. So 2b fixed a live
bug, and the end state is that azure and gcp are correctly never suppressed.
The entries also guard a future divergence, e.g. a generic noun being added to
the azure hint.

Spotted by the coder while doing step 6, not by me when specifying it.

## Step 7 evidence (live, 2026-10-02)

Run as a 2x2 so the code change is isolated from the brief change, because the
obvious single measurement would have been confounded: the deployed PFactory
image does **not** carry this fix, so a live re-ingest of the corrected brief
measures the brief edit, not the code.

Prediction, stated before measuring: unfixed code on the corrected brief should
still report 4, because the deployed code has no precondition at all and
"kubernetes"/"postgres" match the hints regardless of whether RDS is named.

|                  | unfixed code (deployed) | fixed code (this branch) |
| ---------------- | ----------------------- | ------------------------ |
| brief as written | 4                       | 4  (RDS is named)        |
| brief corrected  | **4** — measured live   | **0** — measured         |

Live arm, session `071-pfactory-839-live-check-corrected-brief-on-the-dep`
against the running service:

```
access-verified: fail
Unverified actions: aws:eks:CreateCluster, aws:ec2:RunInstances,
                    aws:iam:CreateRole, aws:rds:CreateDBInstance
```

So the brief correction alone does **not** fix this — the code change is what
does the work, and the brief correction is what lets the code change apply. Had
I measured only the bottom-right cell I could have claimed a fix that the brief
edit had produced on its own.

The top row is the expected behaviour recorded under resolution (a): a plan
naming a provider keeps its actions, even in a denial.

A full live proof of the deployed path has to wait for this branch to be
released into the cluster image; the fixed-code arm above is the same function
the service calls, run on the same brief.

## Review findings, both real (2026-10-02)

Reviewed by a fresh agent given only this plan and the diff. It found the top
risk this plan set out to avoid, live.

### HIGH: suppression ignored the AWS evidence in the same RepoMap

The condition read only `available` and `deploy_system`. `RepoMap` also carries
`cloud_providers`, `iac` and `deploy_manifests` — all populated, none consulted.
Two false negatives, measured by calling `required_actions` on this branch:

- **`deploy_system == "none"` means recon RECOGNISED nothing**, not that nothing
  is provisioned. `probe_iac` knows only terraform/helm/kubernetes, so a CDK /
  SAM / serverless repo — exactly the files `detect_cloud_providers` reads to
  set `cloud_providers=["aws"]` — lands on `"none"`, which is in
  `_NON_CLOUD_DEPLOY_SYSTEMS`.
- **Helm shadows Terraform.** Precedence is argocd > helm > terraform > kubectl,
  so "Terraform provisions RDS, Helm deploys the app" resolves to `"helm"` while
  `iac` and `deploy_manifests` both say terraform.

Measured, all three generic triggers (`postgres`, `bucket`, `redis`) on both
shapes: `[]`. With `cloud_providers: ['aws']` sitting in the same RepoMap.

**My risk assessment in this plan was wrong, and that is the important part.**
I weighed the loss against "a spurious `low`/`soft` line". But
`required_actions` returning `[]` makes `verify_access` return at access.py:126
**before any IAM simulation runs**, so the `severity="high"` "Principal cannot
`rds:CreateDBInstance"` finding at access.py:175 never fires. A genuine
`explicitDeny` becomes silence that surfaces at handoff. The trade I described
was not the trade being made.

Fixed with `_repo_targets_provider`, reading signals already in hand: the
provider named in `cloud_providers`, or provisioning IaC in `iac` or
`deploy_manifests`. Deliberately permissive — it exists to STOP suppression, so
a false positive there only keeps the gate noisy, which is the correct
direction. Three tests; each half mutation-checked independently (drop the
clause → both new tests fail; drop only the IaC half → only the IaC test fails).

### MEDIUM: my step-6 claim was false

I wrote that correction 2b "fixed a live bug" because "with no azure entry,
`_mentions_provider` returned `False` and a plan naming AKS was suppressible".
That describes the state *before* the fail-open change, not after. Since
`_mentions_provider` now returns `True` for a provider with no entry, deleting
the azure and gcp entries changes suppression behaviour **not at all** —
confirmed: `test_required_actions_azure_and_gcp_hints_unaffected_by_the_aws_guard`
still passes with both entries deleted.

So 2b contained two fixes, either of which alone would have closed the hole, and
I credited the entries with work the fail-open default does. The entries remain
worth keeping as documentation of which terms name which provider, but they are
not load-bearing and this plan should not have claimed they were.

## Tests

```sh
V=apps/backend/.venv/bin
$V/python -m pytest tests/ -q -k "access or feasibility"
$V/python -m pytest tests/ -q
$V/python -m pytest tests/test_access.py -q      # collected alone
$V/ruff check apps/backend tests scripts
$V/ruff format --check apps/backend tests scripts
$V/python scripts/ratchet_lint.py --base origin/dev \
  --package apps/backend --package scripts
```

Expected: step 1 answers the ordering question before anything else; steps 4-5
fail before step 3 and pass after; all three mutations fail; the live re-ingest
reports no AWS actions.

## Rollback

Revert the PR. The hints table matches prose again and the four spurious actions
return. Nothing persists — `required_actions` is computed per plan, and already
stored plans keep whatever they were stamped with either way.
