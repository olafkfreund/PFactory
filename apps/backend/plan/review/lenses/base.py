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
        completeness,
        compliance,
        feasibility,
        security,
    )

    order = [
        "feasibility",
        "architecture",
        "security",
        "compliance",
        "best-practices",
        "completeness",
    ]

    # RFC-0015 §4 D1: the adversarial red-team lens is gated — only register it in
    # the default set when the declarative extension registry (D3) enables it (or
    # an operator opts in via env). Until then it is absent, so it costs nothing.
    from plan.review.extension_registry import is_enabled  # noqa: PLC0415

    red_team_on = is_enabled("red-team-review")
    if red_team_on:
        from plan.review.lenses import red_team  # noqa: F401, PLC0415

        order.append("red-team")

    seen = list(order)
    # Any other registered lens (e.g. a test-injected one) tails the list, but the
    # gated red-team lens is excluded unless explicitly enabled above — once its
    # module is imported it lives in _REGISTRY, and we must not leak it back in.
    for n in _REGISTRY:
        if n in order:
            continue
        if n == "red-team" and not red_team_on:
            continue  # gated lens never leaks back in via the registry tail
        seen.append(n)
    return [_REGISTRY[n] for n in seen if n in _REGISTRY]
