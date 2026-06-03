"""Lens protocol + registry for the multi-lens review gates (issue #15).

A *lens* evaluates a ``(plan, epic)`` pair through one perspective
(architecture, security, best-practices, feasibility) and returns a
:class:`~plan.review.models.LensScore` — a 0–1 score plus
:class:`~plan.review.models.Finding` notes. Lenses are deterministic heuristics
by default; an optional LLM seam may refine them but is never required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan
    from plan.review.models import LensScore


@runtime_checkable
class Lens(Protocol):
    """One review perspective over a plan + its decomposed epic."""

    name: str

    def evaluate(self, plan: NormalizedPlan, epic: EpicPlan) -> LensScore:
        """Score the plan/epic through this lens."""
        ...


_REGISTRY: dict[str, Lens] = {}


def register_lens(lens: Lens) -> Lens:
    """Register (or replace) a lens by its ``name`` and return it."""
    _REGISTRY[lens.name] = lens
    return lens


def get_lens(name: str) -> Lens | None:
    """Look up a registered lens by name."""
    return _REGISTRY.get(name)


def default_lenses() -> list[Lens]:
    """Return the built-in lenses in evaluation order.

    Imported lazily so registration (a side-effect of importing each lens
    module) happens on first use without an import cycle.
    """
    from plan.review.lenses import (  # noqa: F401
        architecture,
        best_practices,
        feasibility,
        security,
    )

    order = ["feasibility", "architecture", "security", "best-practices"]
    seen = list(order)
    seen.extend(n for n in _REGISTRY if n not in order)
    return [_REGISTRY[n] for n in seen if n in _REGISTRY]
