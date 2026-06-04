"""Review gate orchestration (issues #15 + #16).

:func:`run_gates` is the single entry point that ties the two halves together:
the scored multi-lens review (#15) and the deterministic rules engine (#16). It
runs the default lenses, folds rule-engine + external-policy findings into the
most relevant lens (secrets → security, etc.), and returns a recomputed
:class:`~plan.review.models.PlanReview`.

Code-specific lenses (architecture/security) are only weighted for *software*
plans; ``code_gates_applied`` reflects this. Non-software plans are never
penalised for missing testing/cicd children.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from plan.review.lenses.base import default_lenses
from plan.review.models import Finding, LensScore, PlanReview
from plan.review.rules.engine import run_external_policy, run_rules

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan
    from plan.review.lenses.base import Lens

# Which lens absorbs a rule/policy finding, keyed by the finding's source.
_SOURCE_TO_LENS: dict[str, str] = {
    "no-plaintext-secrets": "security",
    "public-exposure": "security",
    "checkov": "security",
    "opa": "security",
    "cloud-mcp": "security",
    "infra-change-needs-rollback": "feasibility",
    "k8s-workloads-need-limits": "architecture",
    "feasibility-cost": "feasibility",
    "feasibility-effort": "feasibility",
    "feasibility-access": "feasibility",
}
# Lenses that only make sense for software targets.
_CODE_LENSES = {"architecture", "security"}
# Severity → score penalty applied to the absorbing lens.
_SEVERITY_PENALTY = {
    "info": 0.0,
    "low": 0.05,
    "medium": 0.15,
    "high": 0.3,
    "critical": 1.0,
}


def run_gates(
    plan: NormalizedPlan,
    epic: EpicPlan,
    *,
    lenses: list[Lens] | None = None,
    rules: bool = True,
    threshold: float = 0.75,
    external_runner: Callable[[NormalizedPlan, EpicPlan], list[Finding]] | None = None,
) -> PlanReview:
    """Evaluate a plan/epic through the lenses + rules and return a PlanReview."""
    is_software = plan.target_kind == "software"
    active = lenses if lenses is not None else default_lenses()

    scores: dict[str, LensScore] = {}
    ordered: list[str] = []
    for lens in active:
        # For non-software plans, code-specific lenses don't gate the outcome.
        if not is_software and lens.name in _CODE_LENSES:
            continue
        ls = lens.evaluate(plan, epic)
        scores[ls.lens] = ls
        ordered.append(ls.lens)

    # Collect deterministic + external policy findings, then fold them in.
    extra: list[Finding] = []
    if rules:
        extra.extend(run_rules(plan, epic))
    extra.extend(run_external_policy(plan, epic, runner=external_runner))

    _absorb_findings(extra, scores, ordered, is_software=is_software)

    review = PlanReview(
        plan_id=plan.plan_id,
        lenses=[scores[name] for name in ordered],
        threshold=threshold,
        code_gates_applied=is_software,
    )
    return review.recompute()


def _absorb_findings(
    findings: list[Finding],
    scores: dict[str, LensScore],
    ordered: list[str],
    *,
    is_software: bool,
) -> None:
    """Merge rule/policy findings into the most relevant active lens.

    Findings whose target lens is inactive (e.g. a security finding on a
    non-software plan) land in a synthesised ``policy`` contribution so they are
    never silently dropped.
    """
    for finding in findings:
        target = _SOURCE_TO_LENS.get(finding.source, "best-practices")
        if target not in scores:
            target = _ensure_policy_lens(scores, ordered)
        ls = scores[target]
        ls.findings.append(finding)
        if finding.blocking:
            ls.blocking = True
        penalty = _SEVERITY_PENALTY.get(finding.severity, 0.1)
        ls.score = round(max(0.0, ls.score - penalty), 4)


def _ensure_policy_lens(scores: dict[str, LensScore], ordered: list[str]) -> str:
    """Create (once) an extra 'policy' lens contribution for orphan findings."""
    if "policy" not in scores:
        scores["policy"] = LensScore(lens="policy", score=1.0)
        ordered.append("policy")
    return "policy"
