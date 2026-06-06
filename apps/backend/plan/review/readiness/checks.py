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

from collections.abc import Callable
from typing import TYPE_CHECKING

from plan.enrich.relevance import is_cloud_relevant
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


CheckFn = Callable[["NormalizedPlan", "EpicPlan", ReadinessContext], ReadinessCheckResult]

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
        detail="" if ok else "The plan produced no child issues — it cannot be executed.",
        remediation="" if ok else "Revise the plan so it can be decomposed into work units.",
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
        detail="" if ok else "No explicit acceptance criteria — execution intent is implicit.",
        remediation="" if ok else "Add an '## Acceptance Criteria' section (or AC#N: lines).",
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
        remediation="" if ok else "Fix depends_on to reference existing child keys; remove cycles.",
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
        remediation="" if ok else "Grant the denied IAM action(s) to the principal, or rescope the plan.",
        evidence={} if ok else {"denied": denied},
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
) -> ReadinessReport:
    """Run every readiness check and collect the results into a report."""
    ctx = ReadinessContext(blocking_findings=blocking_findings or [])
    fns = checks if checks is not None else default_checks()
    results = [fn(plan, epic, ctx) for fn in fns]
    return ReadinessReport(
        plan_id=plan.plan_id,
        plan_hash=plan.content_hash or plan.compute_hash(),
        results=results,
    )
