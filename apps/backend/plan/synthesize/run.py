"""Synthesize-stage runner (issues #13/#14).

Runs both synthesizers (Testing Strategy + CI/CD pipeline) over a plan, attaches
each generated child issue to the epic (de-duplicating by key), and returns the
artifacts so the caller can write the spec documents to disk and emit the issues.
"""

from __future__ import annotations

from plan.decompose.models import EpicPlan
from plan.models import NormalizedPlan
from plan.plan_types import PlanTypeDescriptor, select_for
from plan.synthesize.cicd_generator import generate_cicd
from plan.synthesize.models import SynthesizedArtifact
from plan.synthesize.testing_strategy import generate_testing_strategy


def synthesize(
    plan: NormalizedPlan,
    epic: EpicPlan,
    *,
    descriptor: PlanTypeDescriptor | None = None,
) -> list[SynthesizedArtifact]:
    """Run the testing + CI/CD synthesizers and attach their children to ``epic``.

    Synthesize is the **authoritative** owner of the dedicated testing and CI/CD
    child issues. The Decompose stage may emit lightweight testing/cicd stubs; for
    every artifact this runner produces it removes any pre-existing child of the
    same *kind* (the stub) and attaches the richer synthesized child instead,
    wiring it to depend on the feature work. Idempotent (re-running replaces, not
    duplicates). Generators return ``None`` when their stage is disabled for the
    plan type. Returns the produced artifacts.
    """
    descriptor = descriptor or select_for(plan)

    artifacts: list[SynthesizedArtifact] = []
    for generate in (generate_testing_strategy, generate_cicd):
        artifact = generate(plan, descriptor=descriptor)
        if artifact is not None:
            artifacts.append(artifact)

    # Feature/task children the synthesized work should follow.
    feature_keys = [c.key for c in epic.children if c.kind in ("feature", "task")]
    synthesized_kinds = {a.child.kind for a in artifacts}

    # Drop any decompose stubs of the kinds we're synthesizing (authoritative).
    epic.children = [c for c in epic.children if c.kind not in synthesized_kinds]

    existing_keys = {c.key for c in epic.children}
    for artifact in artifacts:
        child = artifact.child
        if not child.depends_on:
            child.depends_on = list(feature_keys)
        if child.key not in existing_keys:
            epic.children.append(child)
            existing_keys.add(child.key)

    return artifacts
