"""Testing Strategy synthesizer (issue #14).

Deterministically renders a markdown Testing Strategy spec for a
:class:`~plan.models.NormalizedPlan` and a dedicated ``testing`` child issue. The
strategy defines test lanes (unit / integration / e2e), coverage targets, and a
table mapping every acceptance criterion to a test approach. The child is
TFactory-aware (labelled ``handover:tfactory``) so the test work routes to the
testing factory.
"""

from __future__ import annotations

from plan.decompose.models import ChildIssue
from plan.models import NormalizedPlan
from plan.plan_types import PlanTypeDescriptor, select_for
from plan.synthesize.models import SynthesizedArtifact

# Coverage targets per lane (deterministic; tuned per plan type if desired).
_COVERAGE_TARGETS: list[tuple[str, str, str]] = [
    ("unit", "80% line / 70% branch", "Pure functions, modules, and edge cases in isolation."),
    (
        "integration",
        "Critical paths covered",
        "Component boundaries, persistence, and external collaborators (stubbed or containerised).",
    ),
    (
        "e2e",
        "All happy paths + key failures",
        "End-to-end user / API journeys against a running stack.",
    ),
]

# Keyword → suggested test lane for acceptance-criterion mapping.
_E2E_HINTS = ("user", "ui", "page", "flow", "login", "screen", "browser", "click")
_INTEGRATION_HINTS = (
    "api",
    "endpoint",
    "database",
    "persist",
    "store",
    "queue",
    "webhook",
    "integration",
    "service",
)


def _approach_for(text: str) -> str:
    """Pick a test lane + approach for one acceptance criterion's text."""
    low = text.lower()
    if any(h in low for h in _E2E_HINTS):
        return "e2e — drive the full flow and assert the observable outcome"
    if any(h in low for h in _INTEGRATION_HINTS):
        return "integration — exercise the boundary with a real/stubbed collaborator"
    return "unit — assert behaviour directly with focused cases"


def generate_testing_strategy(
    plan: NormalizedPlan,
    *,
    descriptor: PlanTypeDescriptor | None = None,
) -> SynthesizedArtifact | None:
    """Synthesize a Testing Strategy spec + child issue for ``plan``.

    Returns ``None`` when the plan's type does not enable the
    ``synthesize_testing`` stage (e.g. non-software / infra-change plans).
    """
    descriptor = descriptor or select_for(plan)
    if not descriptor.stages.synthesize_testing:
        return None

    document = _render_document(plan, descriptor)
    child = _build_child(plan, descriptor)

    return SynthesizedArtifact(
        kind="testing",
        title=f"Testing strategy for {plan.title}",
        document=document,
        child=child,
        filename=f"docs/plans/{plan.plan_id}-testing-strategy.md",
    )


def _render_document(plan: NormalizedPlan, descriptor: PlanTypeDescriptor) -> str:
    """Build the markdown Testing Strategy, including the AC → approach table."""
    lines: list[str] = [
        f"# Testing Strategy — {plan.title}",
        "",
        f"> Plan: `{plan.plan_id}` · Plan type: `{descriptor.name}`",
        "",
        "## Overview",
        "",
        "Layered testing strategy for this plan. Tests are organised into lanes; "
        "each acceptance criterion is mapped to the lane and approach that best "
        "verifies it. Test generation and execution hand over to TFactory.",
        "",
        "## Test Lanes & Coverage Targets",
        "",
        "| Lane | Coverage target | Scope |",
        "| --- | --- | --- |",
    ]
    for lane, target, scope in _COVERAGE_TARGETS:
        lines.append(f"| {lane} | {target} | {scope} |")
    lines.append("")

    lines += [
        "## Acceptance Criteria → Test Approach",
        "",
        "| AC | Criterion | Test approach |",
        "| --- | --- | --- |",
    ]
    if plan.criteria:
        for c in plan.criteria:
            approach = _approach_for(c.text)
            text = c.text.replace("|", "\\|")
            lines.append(f"| `{c.id}` | {text} | {approach} |")
    else:
        lines.append("| — | _No acceptance criteria defined on the plan._ | — |")
    lines.append("")

    lines += [
        "## Definition of Done",
        "",
        "- Every acceptance criterion above has at least one passing test.",
        "- Coverage targets per lane are met or explicitly waived with a reason.",
        "- New tests run in CI and gate the build.",
        "",
    ]
    return "\n".join(lines)


def _build_child(plan: NormalizedPlan, descriptor: PlanTypeDescriptor) -> ChildIssue:
    """Build the TFactory-aware ``testing`` child issue."""
    acceptance: list[str] = [
        "Unit, integration, and e2e lanes are scaffolded and runnable.",
        "Every plan acceptance criterion maps to at least one passing test.",
        "Coverage targets per lane are met or explicitly waived.",
        "Tests run in CI and gate the build.",
    ]
    body = (
        f"Implement the testing strategy specified in "
        f"`docs/plans/{plan.plan_id}-testing-strategy.md`.\n\n"
        "Lanes: unit / integration / e2e. Each acceptance criterion is mapped to "
        "a test approach in the spec. Hand test generation over to TFactory."
    )

    return ChildIssue(
        key="TEST",
        title=f"Set up testing for {plan.title}",
        body=body,
        kind="testing",
        labels=[
            "pfactory",
            "area:testing",
            "handover:tfactory",
            f"plan-type:{descriptor.name}",
        ],
        complexity="standard",
        acceptance_criteria=acceptance,
    )
