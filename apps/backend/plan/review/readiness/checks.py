"""The readiness check catalog + runner (epic #33, P0.2 / P0.4).

Mirrors the rules engine (``plan/review/rules/engine.py``): a ``@check`` registry
of small, pure, deterministic functions. Each returns exactly one
:class:`~plan.review.readiness.models.ReadinessCheckResult`. A *hard* ``fail``
blocks emission unless a human records a waiver; an *advisory* fail informs but
never blocks.

Checks that need context beyond the plan/epic (e.g. the review's blocking
findings) receive a :class:`ReadinessContext`. The runner assembles the context;
callers in ``run_gates`` populate it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from plan.enrich.relevance import is_cloud_relevant
from plan.recon.delta import blast_radius, compute_footprints
from plan.recon.language_reconcile import reconcile_language
from plan.review.models import Finding
from plan.review.readiness.models import ReadinessCheckResult, ReadinessReport
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan


class ReadinessContext(BaseModel):
    """Extra inputs some checks need beyond the plan + epic."""

    # Blocking review findings (from lenses/rules) — read by no-blocking-findings
    # so the readiness report is a single audit surface for "why can't this emit".
    blocking_findings: list[Finding] = Field(default_factory=list)

    # Result of the local-cluster build/run feasibility probe
    # (agents.cloud.local_cluster.probe_cluster().to_dict()), or None when no probe
    # ran. Read by env-buildable. None → the check is not_applicable, so this is
    # fully inert until a caller opts in by injecting a probe result.
    local_cluster: dict | None = None


CheckFn = Callable[
    ["NormalizedPlan", "EpicPlan", ReadinessContext], ReadinessCheckResult
]

_REGISTRY: dict[str, CheckFn] = {}
_ORDER: list[str] = []


def register_check(check_id: str, fn: CheckFn) -> CheckFn:
    """Register (or replace) a check by id, preserving first-seen order."""
    if check_id not in _REGISTRY:
        _ORDER.append(check_id)
    _REGISTRY[check_id] = fn
    return fn


def check(check_id: str) -> Callable[[CheckFn], CheckFn]:
    """Decorator: register a function as a named readiness check."""

    def _wrap(fn: CheckFn) -> CheckFn:
        register_check(check_id, fn)
        return fn

    return _wrap


# ── checks ───────────────────────────────────────────────────────────────


@check("children-present")
def _children_present(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """The epic must decompose into at least one child issue."""
    ok = bool(epic.children)
    return ReadinessCheckResult(
        check_id="children-present",
        title="Epic has at least one child issue",
        status="pass" if ok else "fail",
        severity="info" if ok else "critical",
        hard=True,
        waivable=False,
        detail=""
        if ok
        else "The plan produced no child issues — it cannot be executed.",
        remediation=""
        if ok
        else "Revise the plan so it can be decomposed into work units.",
    )


@check("criteria-present")
def _criteria_present(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """The plan must carry at least one explicit acceptance criterion.

    Detects the silent decomposer fallback that synthesizes a single feature from
    the title/description when no criteria were supplied.
    """
    ok = bool(plan.criteria)
    return ReadinessCheckResult(
        check_id="criteria-present",
        title="Plan has explicit acceptance criteria",
        status="pass" if ok else "fail",
        severity="info" if ok else "high",
        hard=True,
        waivable=True,
        detail=""
        if ok
        else "No explicit acceptance criteria — execution intent is implicit.",
        remediation=""
        if ok
        else "Add an '## Acceptance Criteria' section (or AC#N: lines).",
    )


@check("language-reconciled")
def _language_reconciled(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """The spec's intended language must reconcile with the repo's (RFC-0010, #585).

    Only meaningful once reconnaissance has read the repo. When the spec asks for
    a different language than the repo carries and this is not a migration, the
    plan would silently produce the repo's language — the #585 trap. HALT instead
    (hard fail): correct the spec, re-classify as a migration, or waive.
    """
    rm = plan.repo_map
    if rm is None or not rm.available:
        return ReadinessCheckResult(
            check_id="language-reconciled",
            title="Spec language reconciles with the repo",
            status="not_applicable",
            detail="No reconnaissance grounding (greenfield) — nothing to reconcile.",
        )
    rec = reconcile_language(plan, rm, plan.change_mode or "modify")
    if not rec.conflict:
        return ReadinessCheckResult(
            check_id="language-reconciled",
            title="Spec language reconciles with the repo",
            status="pass",
            detail=(
                f"resolved language: {rec.resolved_language or 'unspecified'}"
                + (f" (repo: {rec.repo_language})" if rec.repo_language else "")
            ),
            evidence={
                "spec_language": rec.spec_language,
                "repo_language": rec.repo_language,
                "resolved_language": rec.resolved_language,
                "change_mode": plan.change_mode,
            },
        )
    return ReadinessCheckResult(
        check_id="language-reconciled",
        title="Spec language reconciles with the repo",
        status="fail",
        severity="high",
        hard=True,
        waivable=True,
        detail=rec.reason,
        remediation=(
            "Align the spec with the repo's language, re-classify as a migration "
            "(rewrite from X to Y), or record a deliberate waiver."
        ),
        evidence={
            "spec_language": rec.spec_language,
            "repo_language": rec.repo_language,
            "change_mode": plan.change_mode,
        },
    )


@check("change-footprint-surfaced")
def _change_footprint_surfaced(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """A modify/migration plan must be grounded in a repo it actually read (RFC-0010).

    Surfaces what the plan will touch (files + blast radius) so the human approves
    a grounded delta, not prose. Hard-fails when the plan claims to modify/migrate
    a repo but reconnaissance never read it (the exact silent-guess failure this
    feature exists to prevent). Greenfield → not_applicable.
    """
    mode = plan.change_mode
    rm = plan.repo_map
    if mode not in ("modify", "migration"):
        return ReadinessCheckResult(
            check_id="change-footprint-surfaced",
            title="Change footprint surfaced for review",
            status="not_applicable",
            detail="Greenfield plan — no existing-code footprint to surface.",
        )
    if rm is None or not rm.available:
        return ReadinessCheckResult(
            check_id="change-footprint-surfaced",
            title="Change footprint surfaced for review",
            status="fail",
            severity="high",
            hard=True,
            waivable=True,
            detail=(
                f"Plan is change_mode={mode} but reconnaissance could not read the "
                f"repo ({rm.error if rm else 'no repo_map'}). It would modify code "
                "it never saw."
            ),
            remediation=(
                "Ensure the target repo + base_ref are set and readable (recon "
                "token for private repos), or waive to proceed greenfield-style."
            ),
        )
    footprints = compute_footprints(plan, epic)
    radius = blast_radius(footprints, rm)
    return ReadinessCheckResult(
        check_id="change-footprint-surfaced",
        title="Change footprint surfaced for review",
        status="pass",
        detail=(
            f"Grounded at {rm.repo}@{(rm.commit or '')[:8]}; "
            f"touches {len(radius.get('files', []))} file(s)."
            + (
                f" Destructive IaC: {', '.join(radius['destructive_iac'])}."
                if radius.get("destructive_iac")
                else ""
            )
        ),
        severity="high" if radius.get("destructive_iac") else "info",
        evidence={"change_mode": mode, **radius},
    )


def _ac_covered(criterion_text: str, epic: EpicPlan) -> bool:
    needle = criterion_text.strip()
    if not needle:
        return False
    for child in epic.children:
        if any(needle == ac.strip() for ac in child.acceptance_criteria):
            return True
        if needle in (child.body or ""):
            return True
    return False


@check("ac-child-coverage")
def _ac_child_coverage(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """Every plan criterion must map to at least one child issue."""
    if not plan.criteria:
        return ReadinessCheckResult(
            check_id="ac-child-coverage",
            title="Every criterion maps to a child issue",
            status="not_applicable",
            detail="Plan has no explicit criteria to map (see criteria-present).",
            hard=True,
            waivable=True,
        )
    uncovered = [c.id for c in plan.criteria if not _ac_covered(c.text, epic)]
    ok = not uncovered
    return ReadinessCheckResult(
        check_id="ac-child-coverage",
        title="Every criterion maps to a child issue",
        status="pass" if ok else "fail",
        severity="info" if ok else "high",
        hard=True,
        waivable=True,
        detail="" if ok else f"Criteria with no child issue: {', '.join(uncovered)}.",
        remediation="" if ok else "Decompose so each acceptance criterion has a child.",
        evidence={} if ok else {"uncovered_acs": uncovered},
    )


@check("service-requirements-covered")
def _service_requirements_covered(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """A deployable service must demand it actually runs (RFC-0008 §3.1, #166).

    Users state feature intent but never write the implicit runtime requirements
    (boots / declares dependencies / health check / deployable), so a build can
    satisfy every stated AC yet not run. ``inject_into_epic`` adds them at plan
    time; this check is the teeth — a hard fail if any remain uncovered (e.g. an
    LLM decompose path that bypassed injection on a child-less epic).
    """
    from plan import plan_types
    from plan.decompose.implicit_requirements import (
        is_deployable_service,
        missing_requirements,
    )

    descriptor = plan_types.select_for(plan)
    if not is_deployable_service(plan, descriptor):
        return ReadinessCheckResult(
            check_id="service-requirements-covered",
            title="Deployable service covers implicit runtime requirements",
            status="not_applicable",
            detail="Not a deployable software service — runtime ACs don't apply.",
            hard=True,
            waivable=True,
        )
    missing = [key for key, _text in missing_requirements(epic)]
    ok = not missing
    return ReadinessCheckResult(
        check_id="service-requirements-covered",
        title="Deployable service covers implicit runtime requirements",
        status="pass" if ok else "fail",
        severity="info" if ok else "high",
        hard=True,
        waivable=True,
        detail=""
        if ok
        else (
            "Implicit service requirements with no acceptance criterion: "
            f"{', '.join(missing)}. The build could pass every stated AC yet "
            "not run."
        ),
        remediation=""
        if ok
        else "Add ACs for: boots, declares dependencies, health check, deployable.",
        evidence={} if ok else {"missing_requirements": missing},
    )


@check("deps-sound")
def _deps_sound(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """Child dependencies must be sane: no self/dangling deps, no cycles.

    A cycle is non-waivable (it makes the build order impossible); dangling/self
    deps are waivable (a human may intend an external or future dependency).
    """
    problems = epic.validate_dependencies()
    ok = not problems
    has_cycle = any("cycle" in p for p in problems)
    return ReadinessCheckResult(
        check_id="deps-sound",
        title="Child dependency graph is sound",
        status="pass" if ok else "fail",
        severity="info" if ok else "high",
        hard=True,
        waivable=not has_cycle,
        detail="" if ok else "; ".join(problems),
        remediation=""
        if ok
        else "Fix depends_on to reference existing child keys; remove cycles.",
        evidence={} if ok else {"problems": problems},
    )


@check("access-granted")
def _access_granted(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """No required capability may be explicitly denied (IAM granted is False)."""
    reqs = list(epic.access_requirements)
    for child in epic.children:
        reqs.extend(child.access_requirements)
    if not reqs:
        return ReadinessCheckResult(
            check_id="access-granted",
            title="No required access is denied",
            status="not_applicable",
            detail="No access requirements were derived for this plan.",
            hard=True,
            waivable=True,
        )
    denied = [f"{r.provider}:{r.action}" for r in reqs if r.granted is False]
    ok = not denied
    return ReadinessCheckResult(
        check_id="access-granted",
        title="No required access is denied",
        status="pass" if ok else "fail",
        severity="info" if ok else "high",
        hard=True,
        waivable=True,
        detail="" if ok else f"Denied actions: {', '.join(denied)}.",
        remediation=""
        if ok
        else "Grant the denied IAM action(s) to the principal, or rescope the plan.",
        evidence={} if ok else {"denied": denied},
    )


@check("env-buildable")
def _env_buildable(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """The target local cluster can actually build/run the proposed workload.

    Consumes the read-only local-cluster probe result that a caller injects into
    :attr:`ReadinessContext.local_cluster` (see ``agents.cloud.local_cluster``).
    Honest degradation:

      - not_applicable → no probe ran (``ctx.local_cluster is None``). This is the
        default, so the check is inert until a caller opts in — it never blocks a
        plan that was never probed.
      - pass           → the cluster is reachable, the namespace exists, and a pod
                         can be scheduled (``buildable`` is true).
      - fail (hard)    → a probe ran and a prerequisite is missing; emission is
                         blocked unless waived (e.g. "the cluster will exist at
                         deploy time"). Mirrors access-granted semantics.
    """
    probe = ctx.local_cluster
    if not probe:
        return ReadinessCheckResult(
            check_id="env-buildable",
            title="Target cluster can build/run the workload",
            status="not_applicable",
            detail="No local-cluster probe ran for this plan.",
            hard=True,
            waivable=True,
        )
    ok = bool(probe.get("buildable"))
    failed = [c for c in probe.get("checks", []) if not c.get("ok")]
    reasons = "; ".join(f"{c.get('id')}: {c.get('detail')}" for c in failed) or (
        probe.get("error") or "unknown"
    )
    return ReadinessCheckResult(
        check_id="env-buildable",
        title="Target cluster can build/run the workload",
        status="pass" if ok else "fail",
        severity="info" if ok else "high",
        hard=True,
        waivable=True,
        detail=""
        if ok
        else f"Local cluster {probe.get('context')!r} is not build-ready: {reasons}.",
        remediation=""
        if ok
        else "Create/reach the cluster + namespace and grant pod scheduling, or "
        "waive if the environment will exist only at deploy time.",
        evidence={} if ok else {"probe": probe},
    )


@check("enrichment-integrity")
def _enrichment_integrity(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """Live-infra enrichment must not have silently failed on a cloud plan.

    Status semantics (graceful degradation):
      - fail            → an adapter RAN and errored (available:false / error)
                          on a cloud-relevant plan — cost/access were computed on
                          partial reality.
      - pass            → adapters ran and all are available ("verified present").
      - not_applicable  → plan isn't cloud-relevant, or no adapters ran at all
                          (disabled / air-gapped) — absence of data never blocks.
    """
    infra = [e for e in plan.enrichment.infra if isinstance(e, dict)]
    failed = [
        str(e.get("adapter") or "?")
        for e in infra
        if e.get("available") is False or e.get("error")
    ]
    relevant = is_cloud_relevant(plan)

    if failed and relevant:
        return ReadinessCheckResult(
            check_id="enrichment-integrity",
            title="Live-infra enrichment is intact",
            status="fail",
            severity="medium",
            hard=True,
            waivable=True,
            detail=f"Infra adapter(s) failed on a cloud-relevant plan: {', '.join(failed)}.",
            remediation="Fix the adapter credentials/connectivity, or waive if 'no cloud' is intended.",
            evidence={"failed_adapters": failed},
        )
    if not infra:
        return ReadinessCheckResult(
            check_id="enrichment-integrity",
            title="Live-infra enrichment is intact",
            status="not_applicable",
            detail="No infra adapters ran (disabled / air-gapped).",
            hard=True,
            waivable=True,
        )
    if failed:  # failed but plan not cloud-relevant
        return ReadinessCheckResult(
            check_id="enrichment-integrity",
            title="Live-infra enrichment is intact",
            status="not_applicable",
            detail="Adapter(s) unavailable, but the plan is not cloud-relevant.",
            hard=True,
            waivable=True,
        )
    return ReadinessCheckResult(
        check_id="enrichment-integrity",
        title="Live-infra enrichment is intact",
        status="pass",
        hard=True,
        waivable=True,
    )


@check("no-blocking-findings")
def _no_blocking_findings(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """No review finding may be blocking (e.g. a hardcoded secret).

    Mirrors the lens gate as a single readiness audit line; never waivable — a
    critical policy violation must be fixed, not waved through.
    """
    blockers = list(ctx.blocking_findings)
    ok = not blockers
    return ReadinessCheckResult(
        check_id="no-blocking-findings",
        title="No blocking review findings",
        status="pass" if ok else "fail",
        severity="info" if ok else "critical",
        hard=True,
        waivable=False,
        detail="" if ok else "; ".join(f.title for f in blockers),
        remediation="" if ok else "Resolve the blocking security/policy finding.",
        evidence={} if ok else {"blocking": [f.title for f in blockers]},
    )


@check("decompose-trustworthy")
def _decompose_trustworthy(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """Decomposition must not have silently fallen back from the LLM (gap #6).

    When the LLM seam was requested but errored, ``decompose_with_llm`` records
    ``decompose_method == "llm_fallback"`` and the error string instead of
    silently returning the heuristic. A fallback is a hard (but waivable) failure
    — an operator may have intended the heuristic, but it must be acknowledged.
    """
    fell_back = epic.decompose_method == "llm_fallback"
    return ReadinessCheckResult(
        check_id="decompose-trustworthy",
        title="Decomposition did not silently fall back",
        status="fail" if fell_back else "pass",
        severity="info" if not fell_back else "medium",
        hard=True,
        waivable=True,
        detail=(
            ""
            if not fell_back
            else "The LLM decomposer errored and fell back to the heuristic."
        ),
        remediation=(
            ""
            if not fell_back
            else "Re-run decomposition; inspect the recorded error, or waive to accept the heuristic."
        ),
        evidence={}
        if not fell_back
        else {"decompose_errors": list(epic.decompose_errors)},
    )


_PLACEHOLDER_RE = re.compile(r"\b(?:todo|tbd)\b|\?\?\?", re.IGNORECASE)


def _is_weak_criterion(text: str) -> bool:
    """A criterion is weak when empty, a placeholder, or too vague to test."""
    stripped = text.strip()
    if not stripped:
        return True
    if _PLACEHOLDER_RE.search(stripped):
        return True
    # Too vague: fewer than three words is rarely a measurable acceptance test.
    return len(stripped.split()) < 3


@check("ac-testable")
def _ac_testable(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """Each acceptance criterion should be measurable (advisory).

    Heuristic weak-AC detection: empty, a placeholder (TODO/TBD/???), or too
    vague (< 3 words). Advisory — informs but never blocks emission.
    """
    if not plan.criteria:
        return ReadinessCheckResult(
            check_id="ac-testable",
            title="Acceptance criteria are testable",
            status="not_applicable",
            detail="Plan has no explicit criteria to assess (see criteria-present).",
            hard=False,
            waivable=True,
        )
    weak = [c.id for c in plan.criteria if _is_weak_criterion(c.text)]
    ok = not weak
    return ReadinessCheckResult(
        check_id="ac-testable",
        title="Acceptance criteria are testable",
        status="pass" if ok else "fail",
        severity="info" if ok else "low",
        hard=False,
        waivable=True,
        detail="" if ok else f"Vague/placeholder criteria: {', '.join(weak)}.",
        remediation=""
        if ok
        else "Rewrite each weak criterion as a measurable, verb+object statement.",
        evidence={} if ok else {"weak_acs": weak},
    )


@check("access-verified")
def _access_verified(
    plan: NormalizedPlan, epic: EpicPlan, ctx: ReadinessContext
) -> ReadinessCheckResult:
    """Flag access requirements that were never verified (advisory).

    ``AccessRequirement.granted is None`` means the IAM/quota simulation could not
    confirm or deny the capability. Advisory — informs but never blocks; a human
    may confirm the permission manually.
    """
    reqs = list(epic.access_requirements)
    for child in epic.children:
        reqs.extend(child.access_requirements)
    if not reqs:
        return ReadinessCheckResult(
            check_id="access-verified",
            title="Required access is verified",
            status="not_applicable",
            detail="No access requirements were derived for this plan.",
            hard=False,
            waivable=True,
        )
    unverified = [f"{r.provider}:{r.action}" for r in reqs if r.granted is None]
    ok = not unverified
    return ReadinessCheckResult(
        check_id="access-verified",
        title="Required access is verified",
        status="pass" if ok else "fail",
        severity="info" if ok else "low",
        hard=False,
        waivable=True,
        detail="" if ok else f"Unverified actions: {', '.join(unverified)}.",
        remediation=""
        if ok
        else "Confirm the unverified IAM action(s) manually, or re-run the access check.",
        evidence={} if ok else {"unverified": unverified},
    )


# ── runner ─────────────────────────────────────────────────────────────


def default_checks() -> list[CheckFn]:
    """Return the registered checks in a stable, first-registered order."""
    return [_REGISTRY[cid] for cid in _ORDER if cid in _REGISTRY]


def run_readiness(
    plan: NormalizedPlan,
    epic: EpicPlan,
    *,
    checks: list[CheckFn] | None = None,
    blocking_findings: list[Finding] | None = None,
    local_cluster: dict | None = None,
) -> ReadinessReport:
    """Run every readiness check and collect the results into a report.

    ``local_cluster`` is an optional read-only local-cluster probe result
    (``agents.cloud.local_cluster.probe_cluster().to_dict()``); when omitted the
    ``env-buildable`` check reports not_applicable.
    """
    ctx = ReadinessContext(
        blocking_findings=blocking_findings or [], local_cluster=local_cluster
    )
    fns = checks if checks is not None else default_checks()
    results = [fn(plan, epic, ctx) for fn in fns]
    return ReadinessReport(
        plan_id=plan.plan_id,
        plan_hash=plan.content_hash or plan.compute_hash(),
        results=results,
    )
