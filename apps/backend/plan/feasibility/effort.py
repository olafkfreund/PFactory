"""Effort/time estimation — calibrated rollup of child complexity (Phase C).

Pure and deterministic (no I/O): maps each child's complexity to story points,
sums them, and converts to a duration band using a stated team velocity. Every
assumption is recorded on the estimate so the number is auditable rather than
magic — PFactory flags, the engineer decides.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plan.decompose.models import EffortEstimate

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan

# Fibonacci-ish points per complexity band.
_POINTS: dict[str, int] = {"simple": 1, "standard": 3, "complex": 8}
# Throughput assumptions for the duration band (points completed per dev-day),
# low = conservative, high = optimistic. Stated as an assumption on the estimate.
_VELOCITY_LOW = 1.0
_VELOCITY_HIGH = 2.5


def estimate_effort(epic: EpicPlan) -> EffortEstimate:
    """Roll child complexity into a story-point total + duration band."""
    points = sum(_POINTS.get(c.complexity, 3) for c in epic.children)
    n = len(epic.children)
    complex_n = sum(1 for c in epic.children if c.complexity == "complex")

    if points == 0:
        return EffortEstimate(
            story_points=0,
            confidence="low",
            assumptions=["No children to estimate."],
        )

    days_high = round(points / _VELOCITY_LOW, 1)  # slow velocity → more days
    days_low = round(points / _VELOCITY_HIGH, 1)  # fast velocity → fewer days

    # Confidence drops as the share of "complex" (high-variance) work rises.
    complex_ratio = complex_n / n if n else 0.0
    confidence = "high" if complex_ratio < 0.25 else "medium" if complex_ratio < 0.6 else "low"

    return EffortEstimate(
        story_points=points,
        duration_days_low=days_low,
        duration_days_high=days_high,
        confidence=confidence,
        assumptions=[
            f"{n} child issues; {complex_n} rated 'complex'.",
            f"Points: simple=1, standard=3, complex=8 (total {points}).",
            f"Velocity {_VELOCITY_LOW}–{_VELOCITY_HIGH} pts/dev-day (one developer).",
            "Excludes review/QA latency and external dependencies.",
        ],
    )
