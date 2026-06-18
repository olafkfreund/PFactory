"""Attach the RFC-0010 migration metadata to the contract (Phase 5).

For ``change_mode == migration`` this records both languages on the environment
manifest and adds the ``tfactory.equivalence`` block + lane that TFactory's
differential lane (Phase 6) consumes. Additive + a no-op for non-migration plans.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plan.models import NormalizedPlan


def attach_migration(contract: dict, plan: NormalizedPlan) -> dict:
    """Set environment source/target language + tfactory.equivalence (mutates)."""
    if plan.change_mode != "migration" or not plan.migration:
        return contract
    mig = plan.migration
    target = plan.target_language or mig.get("target_language")
    source = plan.source_language or mig.get("source_language")

    env = contract.setdefault("environment", {})
    if source:
        env["source_language"] = source
    if target:
        env["target_language"] = target
        env["language"] = target  # the deliverable's language

    equivalence = mig.get("equivalence")
    if equivalence:
        tf = contract.setdefault("tfactory", {})
        tf["equivalence"] = equivalence
        lanes = tf.setdefault("lanes", [])
        if "equivalence" not in lanes:
            lanes.append("equivalence")
    return contract
