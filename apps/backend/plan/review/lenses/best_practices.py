"""Best-practices lens (issue #15).

Asks: *does the decomposition follow our engineering norms?* For software plans
it expects testing and CI/CD child issues, and it rewards alignment with any
"golden path" knowledge surfaced during enrichment. Non-software plans are not
penalised for missing testing/cicd children.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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

        # Non-software plans aren't held to software testing/cicd norms.
        if plan.target_kind != "software":
            if plan.enrichment.knowledge:
                findings.append(
                    Finding(
                        title="Golden-path guidance available",
                        detail=(
                            f"{len(plan.enrichment.knowledge)} knowledge reference(s) "
                            "were surfaced; align deliverables with them."
                        ),
                        severity="info",
                        source=self.name,
                    )
                )
            return LensScore(lens=self.name, score=1.0, findings=findings)

        kinds = {c.kind for c in epic.children}

        if "testing" not in kinds:
            score -= 0.3
            findings.append(
                Finding(
                    title="No testing child issue",
                    detail="A software epic should include a dedicated testing task.",
                    severity="medium",
                    source=self.name,
                )
            )
        if "cicd" not in kinds:
            score -= 0.3
            findings.append(
                Finding(
                    title="No CI/CD child issue",
                    detail="A software epic should include a CI/CD pipeline task.",
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
