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

    if is_enabled("red-team-review"):
        from plan.review.lenses import red_team  # noqa: F401, PLC0415

        order.append("red-team")

    seen = list(order)
    # Any other registered lens (e.g. a test-injected one) tails the list — but
    # the tail must fail CLOSED for gated lenses (PFactory#676): before this
    # rule, ANY registered lens was admitted and only a hardcoded red-team
    # exception kept the gated lens out, so a second gated lens would have run
    # while its registry entry still said enabled: false.
    for n in _REGISTRY:
        if n in order or _gated_off(n):
            continue
        seen.append(n)
    return [_REGISTRY[n] for n in seen if n in _REGISTRY]


def _gated_off(lens_name: str) -> bool:
    """True when the declarative registry gates lens ``lens_name`` OFF.

    Gating is declarative (RFC-0015 §4 D3): a lens is governed by the extension
    named ``<lens_name>-review`` when such an entry exists. Present and disabled
    => excluded, however the lens got registered. Enabled or absent => admitted —
    absent covers the mandatory built-ins and test-injected lenses, which have
    no gate to fail. ``is_enabled`` also honours the operator env override
    (``PFACTORY_<NAME>``), so an opt-in still admits a registry-disabled lens.
    """
    from plan.review.extension_registry import get_extension, is_enabled  # noqa: PLC0415

    extension = f"{lens_name}-review"
    if get_extension(extension) is None:
        return False
    return not is_enabled(extension)
