"""Feasibility orchestrator (Phase C) — cost + access in one pass.

:func:`assess_feasibility` runs both assessors, returns the populated estimates,
and emits CITED findings (an info summary for cost, a budget advisory when over
threshold, plus the access findings). It never raises — any assessor failure
degrades to a low-confidence/empty result, never an error.

RFC-0014: the dev-day/story-point effort estimate was removed — sizing an
LLM-agent task in human dev-days is meaningless. Capability is now expressed via
the scorer's difficulty/risk/autonomy verdict on the Task Contract.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from plan.decompose.models import AccessRequirement, CostEstimate
from plan.feasibility.access import verify_access
from plan.feasibility.cost import estimate_cost
from plan.review.models import Citation, Finding
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

_PRICING_DOCS = "https://aws.amazon.com/pricing/"


class FeasibilityResult(BaseModel):
    """Bundle of the feasibility assessments + their review findings."""

    cost: CostEstimate | None = None
    access: list[AccessRequirement] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


def _budget_threshold() -> float | None:
    raw = os.environ.get("PFACTORY_BUDGET_MONTHLY_USD")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def assess_feasibility(plan: NormalizedPlan, epic: EpicPlan) -> FeasibilityResult:
    """Run cost + access; return estimates + cited findings."""
    findings: list[Finding] = []

    # ── cost ──
    try:
        cost = estimate_cost(plan, epic)
    except Exception:
        cost = CostEstimate(confidence="low", source="error")
    if cost.lines:
        findings.append(
            Finding(
                title=f"Estimated cloud cost ≈ ${cost.monthly_usd:,.0f}/mo ({cost.confidence} confidence)",
                detail=(
                    f"{len(cost.lines)} resource line(s) priced via {cost.source}. "
                    "Confidence reflects how much was priced from a live pricing API "
                    "vs. a static fallback."
                ),
                severity="info",
                source="feasibility-cost",
                citations=[
                    Citation(
                        why="Cost must be weighed before committing infra work.",
                        uri=_PRICING_DOCS,
                        title="Cloud pricing",
                        source=cost.source or "pricing",
                    )
                ],
            )
        )
        budget = _budget_threshold()
        if budget is not None and cost.monthly_usd > budget:
            findings.append(
                Finding(
                    title=f"Estimated cost exceeds the ${budget:,.0f}/mo budget",
                    detail=(
                        f"≈ ${cost.monthly_usd:,.0f}/mo vs. a ${budget:,.0f} budget "
                        "(PFACTORY_BUDGET_MONTHLY_USD). Confirm the spend or trim scope."
                    ),
                    severity="high",
                    source="feasibility-cost",
                    blocking=False,
                    citations=[
                        Citation(
                            why="Spending above the agreed budget needs sign-off.",
                            uri=_PRICING_DOCS,
                            title="Cloud pricing",
                            source="budget-policy",
                        )
                    ],
                )
            )

    # ── access ──
    try:
        access, access_findings = verify_access(plan, epic)
    except Exception:
        access, access_findings = [], []
    findings.extend(access_findings)

    return FeasibilityResult(cost=cost, access=access, findings=findings)
