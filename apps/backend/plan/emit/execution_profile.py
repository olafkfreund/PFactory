"""Compute the Task Contract ``execution`` profile (epic #65, child 3).

HOW AIFactory should execute the plan — model + provider + per-phase thinking,
right-sized from the epic's complexity and shape. Provider is always inferred
from the chosen model via :func:`phase_config.infer_provider_from_model` (the
model string is the single source of truth, per the codebase convention).

Parallel-safety / worker count (child 4) and ``review_tier`` (child 6) are filled
by their own children; this step sets complexity, model, provider, phase models +
thinking, the agent roster, and ``skip_planning`` (the whole point — AIFactory
goes straight to the wave executor because PFactory already planned).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from phase_config import infer_provider_from_model

if TYPE_CHECKING:
    from plan.decompose.models import EpicPlan
    from plan.models import NormalizedPlan

_COMPLEXITY_RANK = {"simple": 0, "standard": 1, "complex": 2}
_RANK_COMPLEXITY = {0: "simple", 1: "standard", 2: "complex"}

# Default model per complexity (shorthands resolved by phase_config).
_MODEL_BY_COMPLEXITY = {"simple": "haiku", "standard": "sonnet", "complex": "opus"}

# Per-phase thinking budget per complexity (schema enum: none/low/medium/high).
_THINKING_BY_COMPLEXITY = {
    "simple": {"coding": "low", "qa": "none"},
    "standard": {"coding": "medium", "qa": "low"},
    "complex": {"coding": "high", "qa": "medium"},
}


def derive_complexity(epic: EpicPlan, *, services: int = 0) -> str:
    """Roll the epic's children up to a single complexity band.

    Conservative: take the max child complexity, then bump simple→standard when
    the work spans several services (cross-service coordination is rarely simple).
    """
    if not epic.children:
        return "standard"
    rank = max(_COMPLEXITY_RANK.get(c.complexity, 1) for c in epic.children)
    complexity = _RANK_COMPLEXITY[rank]
    if complexity == "simple" and services >= 3:
        complexity = "standard"
    return complexity


def build_execution(plan: NormalizedPlan, epic: EpicPlan) -> dict[str, Any]:
    """Build the contract's ``execution`` block for ``plan``/``epic``."""
    services = len({
        s
        for c in epic.children
        for label in c.labels
        for prefix in ("service:", "area:")
        if label.startswith(prefix) and (s := label[len(prefix):])
    })
    complexity = derive_complexity(epic, services=services)
    model = _MODEL_BY_COMPLEXITY[complexity]
    provider = infer_provider_from_model(model)

    return {
        "complexity": complexity,
        "model": model,
        "provider": provider,
        "provider_rationale": f"{model!r} → {provider} (inferred from the model id)",
        "phase_models": {"coding": model, "qa": model, "qa_fixer": model},
        "phase_thinking": dict(_THINKING_BY_COMPLEXITY[complexity]),
        "agents": {
            "planner": False,  # PFactory already planned — skip it
            "coder": True,
            "qa_reviewer": True,
            "qa_fixer": True,
        },
        "skip_planning": True,
    }


def attach_execution(
    contract: dict[str, Any], plan: NormalizedPlan, epic: EpicPlan
) -> dict[str, Any]:
    """Attach (in place) the ``execution`` block to a built contract; return it."""
    contract["execution"] = build_execution(plan, epic)
    return contract
