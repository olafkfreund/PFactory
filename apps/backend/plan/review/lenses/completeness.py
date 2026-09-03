"""Completeness lens (RFC-0008 §3.1, #166).

Asks: *for a deployable software service, does the plan demand that the thing
actually runs?* Users state feature intent but never write the implicit runtime
requirements (boots / declares dependencies / health check / deployable), so a
build can pass every stated AC yet not run. This lens scores a service plan on
how many of those implicit requirements are covered by some child acceptance
criterion — the enforcement half of the "complete the plan" work, paired with
the ``service-requirements-covered`` readiness check.

Non-service plans are not applicable and score a clean 1.0 (no findings).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plan import plan_types
from plan.decompose.implicit_requirements import (
    missing_requirements,
    requirement_set,
)
from plan.review.lenses.base import register_lens
from plan.review.models import Finding, LensScore

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

# Each uncovered implicit requirement costs this much of the lens score.
_PENALTY_PER_MISSING = 0.25


class CompletenessLens:
    """Deterministic check that a deployable service's plan demands it runs."""

    name = "completeness"

    def evaluate(self, plan: NormalizedPlan, epic: EpicPlan) -> LensScore:
        descriptor = plan_types.select_for(plan)
        requirements = requirement_set(plan, descriptor)
        if not requirements:
            # This plan type carries no implicit requirements — nothing to
            # enforce. Clean pass, no findings.
            return LensScore(lens=self.name, score=1.0, findings=[])

        missing = missing_requirements(epic, requirements)
        findings: list[Finding] = []
        score = 1.0
        for key, text in missing:
            score -= _PENALTY_PER_MISSING
            findings.append(
                Finding(
                    title=f"Implicit requirement not covered: {key}",
                    detail=(
                        f"A {descriptor.title} must satisfy: {text} "
                        "No child acceptance criterion covers it, so the build "
                        "could pass every stated AC yet miss it."
                    ),
                    severity="high",
                    source=self.name,
                )
            )
        if not missing:
            covered = ", ".join(key for key, _text, _kw in requirements)
            findings.append(
                Finding(
                    title="Implicit requirements covered",
                    detail=(
                        f"All implicit requirements for this plan type "
                        f"({covered}) are demanded by an acceptance criterion."
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


register_lens(CompletenessLens())
