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
from plan.models import NormalizedPlan, slugify
from plan.plan_types import PlanTypeDescriptor, select_for
from plan.recon.language_reconcile import detect_spec_language
from plan.synthesize.models import SynthesizedArtifact

# Where a language keeps its tests, and what one test file is called there.
# Flat on purpose: the lane goes in the *file name*, not a subdirectory, because
# cargo only compiles `tests/*.rs` at the top level and surefire only runs
# `*Test.java` — a `tests/unit/` subdir would be scaffolding that never runs.
#
# This table covers the same languages as plan.recon.delta._CODE_EXTS, and must
# keep doing so in both directions: a language here whose extension the miner
# discards yields a child that names a file nobody is handed, and a language the
# miner can see but that is missing here yields a child that names no file at
# all. It listed seven while the miner knew seven; both now list twelve (#475).
#
# The five added names follow each ecosystem's own discovery rule, which is not
# cosmetic — a test file the runner does not match simply never executes:
#   C#      xUnit/NUnit discover by assembly, `*Tests.cs` is the convention
#   Kotlin  Gradle/JUnit require src/test/kotlin, mirroring the Java entry
#   PHP     PHPUnit's default suffix filter is literally `*Test.php`
#   Swift   SwiftPM only compiles targets under Tests/
#   C/C++   CTest/GoogleTest register per-file; `*_test.cpp` is the convention
_TEST_LAYOUT: dict[str, tuple[str, str]] = {
    "python": ("tests", "test_{name}.py"),
    "typescript": ("tests", "{name}.test.ts"),
    "javascript": ("tests", "{name}.test.js"),
    "go": ("tests", "{name}_test.go"),
    "rust": ("tests", "{name}.rs"),
    "java": ("src/test/java", "{camel}Test.java"),
    "ruby": ("spec", "{name}_spec.rb"),
    "csharp": ("tests", "{camel}Tests.cs"),
    "kotlin": ("src/test/kotlin", "{camel}Test.kt"),
    "php": ("tests", "{camel}Test.php"),
    "swift": ("Tests", "{camel}Tests.swift"),
    "cpp": ("tests", "{name}_test.cpp"),
}
# A test root already in the repo beats the language default. Top-level only —
# that is all reconnaissance's layout scan sees.
_TEST_DIRS = ("tests", "test", "spec", "__tests__")

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


def _language(plan: NormalizedPlan) -> str:
    """The language the tests will be written in: the repo's, else the spec's."""
    repo_map = plan.repo_map
    if repo_map is not None and repo_map.available and repo_map.languages:
        return repo_map.languages[0]
    return detect_spec_language(plan) or ""


def _test_root(plan: NormalizedPlan, default: str) -> str:
    """The repo's own test directory when it has one, else the language default."""
    repo_map = plan.repo_map
    dirs = (repo_map.layout or {}).get("dirs", []) if repo_map is not None else []
    for candidate in _TEST_DIRS:
        if candidate in (dirs or []):
            return candidate
    return default


def test_paths(plan: NormalizedPlan) -> list[str]:
    """The test files the ``testing`` child must create — one per lane.

    This is the whole fix for PFactory#461, the same defect #460 fixed for the
    ``cicd`` child one file over. The child's text is the ONLY place a file
    target can come from: :func:`plan.recon.delta.compute_footprints` mines
    file-like tokens out of ``title + body + acceptance_criteria`` and that
    becomes the contract subtask's ``files_to_create`` / ``files_to_modify``,
    which AIFactory renders to the coder as its file list. The old body named
    only ``docs/plans/<id>-testing-strategy.md`` — a document PFactory never
    writes into the target repo — so the coder was handed one file to create, a
    markdown file, and created it (AIFactory#1113).

    Unlike #460 there is no discovered path to reuse, so this picks a
    convention: the repo's existing test directory if reconnaissance saw one,
    else the language's, with the lane in the file *name* so every file sits
    where its own toolchain looks for it. ``_FILE_TOKEN`` only mines tokens with
    an extension, so naming the directory alone would change the prose and
    nothing else.

    Empty when the language is unknown or has no entry above — an honest empty
    list beats naming ``tests/test_x.py`` at a C# repo (the wrong-language class
    of defect, #585).
    """
    root, template = _TEST_LAYOUT.get(_language(plan), ("", ""))
    if not template:
        return []
    slug = slugify(plan.title).replace("-", "_")  # importable: pytest/go reject "-"
    out = []
    for lane, _target, _scope in _COVERAGE_TARGETS:
        name = f"{slug}_{lane}"
        camel = "".join(part.title() for part in name.split("_"))
        out.append(f"{_test_root(plan, root)}/{template.format(name=name, camel=camel)}")
    return out


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
    files = test_paths(plan)
    if files:
        targets = "Test files to add:\n" + "".join(f"- `{f}`\n" for f in files)
    else:
        # ponytail: no mineable convention for this language, so name the lanes
        # and let the coder pick paths. Add a _TEST_LAYOUT entry when a language
        # starts showing up in real plans (its extension must also be in
        # plan.recon.delta._CODE_EXTS or the footprint will not see it).
        targets = "Add unit, integration and e2e test files in this repo's test directory.\n"

    command = (plan.repo_map.existing_test_command if plan.repo_map is not None else None) or ""
    runner = ""
    if command:
        runner = f"\nThey must run under this repo's existing test command: `{command}`.\n"

    body = (
        "Write the tests for this plan.\n\n"
        + targets
        + runner
        + "\nEvery plan acceptance criterion maps to at least one of these tests; "
        "the mapping table is in the attached testing strategy for reference. "
        "TFactory verifies these lanes, so they have to exist here first.\n\n"
        "The acceptance criteria are about tests that RUN, so this subtask is "
        "done only when the file(s) above exist and pass. Writing a design "
        "document satisfies none of them (AIFactory#1113)."
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
