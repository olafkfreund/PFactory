"""Emit taxonomy — labels + the ``pfactory:meta`` block (Phase H, #6).

Turns a governed plan (plan + epic + review) into the GitHub labels and the
machine-readable metadata block that AIFactory/TFactory read (the "secret
language"). The contract is documented in ``docs/tag-taxonomy.md`` (v1) and the
consumer issues AIFactory#327-331 / TFactory#193-197.

Labels: mandatory ``pfactory`` + routing (``handoff:aifactory``, and
``handoff:tfactory`` when there's testing work) + ``type:*`` + ``plan-type:*`` +
``priority:p0..p3`` + AIFactory's reused ``sev:*``. The ``pfactory:meta`` block
carries cost/effort/access/citations + ``taxonomy: v1``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan
    from plan.review.models import PlanReview

TAXONOMY_VERSION = "v1"

_CATEGORY_TO_TYPE: dict[str, str] = {
    "software": "software",
    "feature": "feature",
    "infrastructure": "infra",
    "hosting": "hosting",
    "testing": "testing",
    "cicd": "cicd",
    "product": "product",
    "data": "software",
    "generic": "software",
}

_SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


def _category(plan: NormalizedPlan) -> str:
    """Resolve the plan's category from its plan-type descriptor."""
    try:
        from plan.plan_types import select_for

        return select_for(plan).category
    except Exception:
        return ""


def _max_severity(review: PlanReview | None) -> str | None:
    if review is None:
        return None
    worst = None
    worst_rank = -1
    for lens in review.lenses:
        for f in lens.findings:
            rank = _SEVERITY_ORDER.index(f.severity) if f.severity in _SEVERITY_ORDER else 0
            if rank > worst_rank:
                worst_rank, worst = rank, f.severity
    return worst


def priority_for(review: PlanReview | None) -> str:
    """p0 if anything blocks, p1 if gates didn't pass, else p2."""
    if review is not None and review.blocking_findings():
        return "p0"
    if review is not None and not review.gates_passed:
        return "p1"
    return "p2"


def risk_for(review: PlanReview | None) -> str:
    if review is not None and review.blocking_findings():
        return "high"
    if review is not None and not review.gates_passed:
        return "medium"
    return "low"


def _access_verified(epic: EpicPlan | None) -> bool | None:
    reqs = list(getattr(epic, "access_requirements", []) or []) if epic else []
    if not reqs:
        return None
    if any(r.granted is False for r in reqs):
        return False
    if all(r.granted is True for r in reqs):
        return True
    return None


def taxonomy_labels(
    plan: NormalizedPlan,
    epic: EpicPlan | None = None,
    review: PlanReview | None = None,
) -> list[str]:
    """The full label set PFactory applies to an emitted epic + children."""
    labels = ["pfactory", "handoff:aifactory"]

    category = _category(plan)
    type_label = _CATEGORY_TO_TYPE.get(category, "software")
    labels.append(f"type:{type_label}")

    if plan.plan_type:
        labels.append(f"plan-type:{plan.plan_type}")

    labels.append(f"priority:{priority_for(review)}")

    sev = _max_severity(review)
    if sev and sev != "info":
        labels.append(f"sev:{sev}")

    # Route testing work to TFactory too.
    if epic is not None and any(c.kind == "testing" for c in epic.children):
        labels.append("handoff:tfactory")

    # De-dup, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def pfactory_meta_block(
    plan: NormalizedPlan,
    epic: EpicPlan | None = None,
    review: PlanReview | None = None,
) -> str:
    """The machine-readable ``<!-- pfactory:meta ... -->`` block for an issue body."""
    cost = getattr(epic, "cost_estimate", None) if epic else None
    effort = getattr(epic, "effort_estimate", None) if epic else None
    access_ok = _access_verified(epic)

    lines = [
        "<!-- pfactory:meta",
        f"plan_id: {plan.plan_id}",
        f"plan_type: {plan.plan_type or ''}",
        f"category: {_category(plan)}",
        f"priority: {priority_for(review)}",
        f"risk: {risk_for(review)}",
    ]
    if cost is not None:
        lines.append(f"cost_monthly_usd: {cost.monthly_usd}")
        lines.append(f"cost_confidence: {cost.confidence}")
    if effort is not None:
        lines.append(f"effort_points: {effort.story_points}")
        lines.append(f"effort_days: [{effort.duration_days_low}, {effort.duration_days_high}]")
    if access_ok is not None:
        lines.append(f"access_verified: {str(access_ok).lower()}")

    citations = _collect_citations(review)
    if citations:
        lines.append("citations:")
        for c in citations:
            lines.append(f"  - why: {c['why']!r}")
            if c["uri"]:
                lines.append(f"    uri: {c['uri']}")
            if c["source"]:
                lines.append(f"    source: {c['source']}")

    lines.append(f"taxonomy: {TAXONOMY_VERSION}")
    lines.append("-->")
    return "\n".join(lines)


def _collect_citations(review: PlanReview | None, limit: int = 8) -> list[dict]:
    if review is None:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for lens in review.lenses:
        for f in lens.findings:
            for c in f.citations:
                key = c.uri or c.why
                if key and key not in seen:
                    seen.add(key)
                    out.append({"why": c.why, "uri": c.uri, "source": c.source})
                    if len(out) >= limit:
                        return out
    return out
