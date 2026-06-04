"""Best-practices lens (issue #15).

Asks: *does the decomposition follow our engineering norms?* It expects testing
and CI/CD child issues *only when the plan type calls for them* — the same stage
toggles (``synthesize_testing`` / ``synthesize_cicd``) the planner reads — so an
infra-change SOW isn't dinged for lacking unit tests it was never meant to carry.
It also rewards alignment with any "golden path" knowledge surfaced in enrichment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plan import plan_types
from plan.review.lenses.base import register_lens
from plan.review.models import Finding, LensScore

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan


class BestPracticesLens:
    """Deterministic engineering-norms heuristics over the decomposition."""

    name = "best-practices"

    def evaluate(self, plan: NormalizedPlan, epic: EpicPlan) -> LensScore:
        findings: list[Finding] = []
        score = 1.0

        # Expect exactly the stages this plan type is supposed to synthesize —
        # data-driven, not a blanket "software needs tests" rule.
        stages = plan_types.select_for(plan).stages
        kinds = {c.kind for c in epic.children}

        if stages.synthesize_testing and "testing" not in kinds:
            score -= 0.3
            findings.append(
                Finding(
                    title="No testing child issue",
                    detail="This plan type should include a dedicated testing task.",
                    severity="medium",
                    source=self.name,
                )
            )
        if stages.synthesize_cicd and "cicd" not in kinds:
            score -= 0.3
            findings.append(
                Finding(
                    title="No CI/CD child issue",
                    detail="This plan type should include a CI/CD pipeline task.",
                    severity="medium",
                    source=self.name,
                )
            )

        # Reward alignment with golden-path knowledge when present.
        if plan.enrichment.knowledge:
            findings.append(
                Finding(
                    title="Golden-path guidance available",
                    detail=(
                        f"{len(plan.enrichment.knowledge)} knowledge reference(s) "
                        "were surfaced; confirm the plan follows them."
                    ),
                    severity="info",
                    source=self.name,
                )
            )

        return LensScore(
            lens=self.name,
            score=round(max(0.0, min(1.0, score)), 4),
            findings=findings,
        )


register_lens(BestPracticesLens())
