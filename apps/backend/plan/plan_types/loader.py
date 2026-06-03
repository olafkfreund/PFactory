"""Plan-type descriptors (issue #7).

A *plan type* gates which downstream pipeline stages run for a given plan
(re-shaping TFactory's framework descriptors). Each descriptor is a YAML file in
this package declaring which ``target_kind`` it applies to, keywords that bias
selection, and the ``stages`` toggles the pipeline reads — so the software deep
path (testing + CI/CD + code gates) is enabled by data, not inline conditionals.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from plan.models import NormalizedPlan, TargetKind
from pydantic import BaseModel, Field

_DESCRIPTOR_DIR = Path(__file__).parent


class Stages(BaseModel):
    """Which pipeline stages run for this plan type."""

    decompose: bool = True
    synthesize_testing: bool = False
    synthesize_cicd: bool = False
    code_gates: bool = False
    review: bool = True


class PlanTypeDescriptor(BaseModel):
    """A declarative plan type loaded from a ``*.yaml`` descriptor."""

    name: str
    title: str
    applies_to: list[TargetKind]
    match_keywords: list[str] = Field(default_factory=list)
    stages: Stages = Field(default_factory=Stages)


@lru_cache(maxsize=1)
def load_descriptors() -> dict[str, PlanTypeDescriptor]:
    """Load and cache all descriptor YAMLs in this package."""
    out: dict[str, PlanTypeDescriptor] = {}
    for f in sorted(_DESCRIPTOR_DIR.glob("*.yaml")):
        data = yaml.safe_load(f.read_text()) or {}
        d = PlanTypeDescriptor(**data)
        out[d.name] = d
    return out


def _plan_text(plan: NormalizedPlan) -> str:
    parts = [plan.title, plan.description, *(c.text for c in plan.criteria)]
    if plan.raw_text:
        parts.append(plan.raw_text)
    return "\n".join(p for p in parts if p).lower()


# Default fallback per target kind when no keyword match wins.
_FALLBACK: dict[str, str] = {
    "software": "software-service",
    "non-software": "generic-deliverable",
    "undetermined": "generic-deliverable",
}


def select_for(plan: NormalizedPlan) -> PlanTypeDescriptor:
    """Pick the best-matching plan type for a (classified) plan.

    Among descriptors that apply to the plan's ``target_kind``, choose the one
    with the most keyword hits in the plan text; ties / no hits fall back to the
    kind's default descriptor.
    """
    descriptors = load_descriptors()
    text = _plan_text(plan)
    candidates = [d for d in descriptors.values() if plan.target_kind in d.applies_to]

    best: PlanTypeDescriptor | None = None
    best_score = -1
    for d in candidates:
        score = sum(1 for kw in d.match_keywords if kw.lower() in text)
        if score > best_score:
            best, best_score = d, score

    if best is not None and best_score > 0:
        return best
    return descriptors[_FALLBACK[plan.target_kind]]


def apply(plan: NormalizedPlan) -> NormalizedPlan:
    """Return a copy of ``plan`` with ``plan_type`` set, re-hashed."""
    descriptor = select_for(plan)
    return plan.model_copy(update={"plan_type": descriptor.name}).with_hash()
