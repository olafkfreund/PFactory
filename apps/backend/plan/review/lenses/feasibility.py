"""Feasibility lens (issue #15).

Asks: *can this plan actually be built as decomposed?* It checks that the epic
was decomposed at all, that the dependency graph is sane
(:meth:`EpicPlan.validate_dependencies`), and that complexity is plausible
relative to the amount of work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plan.review.lenses.base import register_lens
from plan.review.models import Finding, LensScore

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

# A single epic carrying many "complex" children is a feasibility smell.
_COMPLEX_RATIO_LIMIT = 0.6


class FeasibilityLens:
    """Deterministic feasibility heuristics over the epic's structure."""

    name = "feasibility"

    def evaluate(self, plan: NormalizedPlan, epic: EpicPlan) -> LensScore:
        findings: list[Finding] = []
        score = 1.0
        blocking = False

        if not epic.children:
            findings.append(
                Finding(
                    title="Epic was not decomposed",
                    detail="The epic has no child issues, so it cannot be executed.",
                    severity="high",
                    source=self.name,
                    blocking=True,
                )
            )
            return LensScore(
                lens=self.name, score=0.0, findings=findings, blocking=True
            )

        dep_problems = epic.validate_dependencies()
        if dep_problems:
            blocking = any("cycle" in p for p in dep_problems)
            score -= min(0.5, 0.2 * len(dep_problems))
            findings.append(
                Finding(
                    title="Dependency graph is unsound",
                    detail="; ".join(dep_problems),
                    severity="high" if blocking else "medium",
                    source=self.name,
                    blocking=blocking,
                )
            )

        complex_children = [c for c in epic.children if c.complexity == "complex"]
        if complex_children and (
            len(complex_children) / len(epic.children) > _COMPLEX_RATIO_LIMIT
        ):
            score -= 0.15
            findings.append(
                Finding(
                    title="Most children are high-complexity",
                    detail=(
                        f"{len(complex_children)}/{len(epic.children)} children are "
                        "'complex'; consider splitting them into smaller units."
                    ),
                    severity="low",
                    source=self.name,
                )
            )

        return LensScore(
            lens=self.name,
            score=round(max(0.0, min(1.0, score)), 4),
            findings=findings,
            blocking=blocking,
        )


register_lens(FeasibilityLens())
