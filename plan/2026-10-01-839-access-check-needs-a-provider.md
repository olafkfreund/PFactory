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
7. **Live.** Re-ingest the brief from session
   `068-myfriends-web-remediation-v2-of-the-two-critical-f` and read
   `readiness.results[]`: `access-verified` must no longer list
   `aws:eks:CreateCluster, aws:ec2:RunInstances, aws:iam:CreateRole,
   aws:rds:CreateDBInstance`. That measurement opened the issue, so it closes it.
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
