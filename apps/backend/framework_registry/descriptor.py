"""Framework descriptor dataclass — Task 1 (#17).

Each framework PFactory supports gets a YAML descriptor at
``frameworks/{name}/descriptor.yaml``.  This module defines the Python
dataclass that mirrors that schema and is the single authoritative
in-memory representation consumed by the Planner, Gen-Functional, and
Evaluator agents.

The dataclass is **frozen** so it is hashable and can safely be used as a
``dict`` key or placed in a ``set``.

Usage::

    from framework_registry.descriptor import FrameworkDescriptor, RuntimeSpec
    from test_plan.enums import Lane

    desc = FrameworkDescriptor(
        name="pytest",
        language="python",
        lanes=(Lane.UNIT,),
        version_range=">=7.0,<9.0",
        runtime=RuntimeSpec(
            image="pfactory-runner-pytest:latest",
            entrypoint=("python", "-m", "pytest"),
        ),
        manifest_signals=("requirements.txt:pytest",),
        test_path_conventions=("tests/**/test_*.py",),
        templates=(),
        coverage_strategy="cobertura",
        context_block="# pytest context\\nUse pytest fixtures.",
        evaluator_hooks=(),
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from packaging.specifiers import SpecifierSet
from test_plan.enums import Lane


@dataclass(frozen=True)
class RuntimeSpec:
    """Docker image + entrypoint for running a framework's test suite.

    Attributes:
        image: Docker image name (e.g. ``"pfactory-runner-pytest:latest"``).
            Task 7 builds these images; the descriptor references them by name.
        entrypoint: Command tuple used to invoke the test runner inside the
            container (e.g. ``("python", "-m", "pytest", "--tb=short")``).
            Executor appends the specific test file path at invocation time.
    """

    image: str
    entrypoint: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.image:
            raise ValueError("RuntimeSpec.image must not be empty")
        # entrypoint is optional — an empty tuple is valid when the image has
        # a built-in ENTRYPOINT.  The Executor appends the test file path at
        # invocation time.


@dataclass(frozen=True)
class FrameworkDescriptor:
    """Per-framework configuration used by all PFactory agents.

    This is the central artifact of the v0.2 architecture (Decision 1 in
    ``docs/plans/2026-05-28-enterprise-test-frameworks-design.md``).  It is
    loaded from ``frameworks/{name}/descriptor.yaml`` by
    ``framework_registry.loader.load_registry`` and consumed at runtime by:

    * **Planner** — to validate (language, framework) combos and inject the
      framework registry summary into the planning prompt.
    * **Gen-Functional** — to pick the runner image, inject ``context_block``,
      and derive default test-file paths.
    * **Evaluator** — to decide whether ``coverage_delta`` should be ``None``
      (when ``coverage_strategy == "skip"``).

    Attributes:
        name: Framework identifier — must match the YAML's ``name`` field and
            the directory name under ``frameworks/``.  E.g. ``"playwright"``.
        language: Primary language this descriptor targets.
            E.g. ``"typescript"``, ``"python"``.
        lanes: Which PFactory lanes this framework lights.
            E.g. ``(Lane.BROWSER,)`` for Playwright.
        version_range: Version specifier string (PEP 440 / packaging syntax).
            E.g. ``">=1.40,<2.0"``.  Stored as the raw string; use the
            ``min_version`` / ``max_version`` properties for programmatic
            access, or ``specifier_set`` for membership testing.
        runtime: Docker image + entrypoint used by the Executor.
        manifest_signals: Patterns the Planner uses to detect whether this
            framework is installed in a project.  Format:
            ``"<filename>:<key-path>"`` — e.g.
            ``"package.json:devDependencies.@playwright/test"``.
        test_path_conventions: Glob patterns describing where tests generated
            by Gen-Functional should land.  E.g.
            ``("tests/e2e/**/*.spec.ts",)``.
        templates: Template filenames this framework ships.  Empty tuple is
            valid until Task 12 populates these.
        coverage_strategy: One of ``"lcov"`` / ``"cobertura"`` / ``"skip"``.
            ``"skip"`` means the framework cannot emit per-test coverage XML
            (browser lane — Decision 11).  The Evaluator converts this to
            ``coverage_delta=None`` (not zero).
        context_block: Markdown block injected into the Gen-Functional prompt
            when generating tests with this framework.  Contains idioms to
            use, anti-patterns to avoid, and framework-specific guidance.
        evaluator_hooks: Dotted-path Python references for per-framework
            Evaluator primitives.  Empty tuple is valid until Task 9 wires
            these.  E.g. ``("agents.lang_typescript.preflight.ts_preflight",)``.
    """

    name: str
    language: str
    lanes: tuple[Lane, ...]
    version_range: str
    runtime: RuntimeSpec
    manifest_signals: tuple[str, ...]
    test_path_conventions: tuple[str, ...]
    templates: tuple[str, ...]
    coverage_strategy: Literal["lcov", "cobertura", "skip"]
    context_block: str
    evaluator_hooks: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate invariants that can't be expressed as type hints."""
        if not self.name:
            raise ValueError("FrameworkDescriptor.name must not be empty")
        if not self.language:
            raise ValueError("FrameworkDescriptor.language must not be empty")
        if not self.lanes:
            raise ValueError("FrameworkDescriptor.lanes must not be empty")
        if self.coverage_strategy not in {"lcov", "cobertura", "skip"}:
            raise ValueError(
                f"coverage_strategy {self.coverage_strategy!r} must be one of "
                "'lcov', 'cobertura', 'skip'"
            )

    @property
    def specifier_set(self) -> SpecifierSet:
        """Return a ``packaging.specifiers.SpecifierSet`` for ``version_range``.

        Example::

            desc.specifier_set.contains("1.50.0")  # True for >=1.40,<2.0
        """
        return SpecifierSet(self.version_range)

    @property
    def min_version(self) -> str | None:
        """Lowest version bound from ``version_range``, or ``None`` if unbounded.

        Returns the specifier's lower-bound string (e.g. ``"1.40"`` for
        ``">=1.40,<2.0"``).  Parses the first ``>=`` or ``>`` specifier in
        the set; returns ``None`` if none is present.
        """
        for spec in SpecifierSet(self.version_range):
            if spec.operator in (">=", ">"):
                return spec.version
        return None

    @property
    def max_version(self) -> str | None:
        """Highest version bound from ``version_range``, or ``None`` if unbounded.

        Returns the specifier's upper-bound string (e.g. ``"2.0"`` for
        ``">=1.40,<2.0"``).  Parses the first ``<=`` or ``<`` specifier in
        the set; returns ``None`` if none is present.
        """
        for spec in SpecifierSet(self.version_range):
            if spec.operator in ("<=", "<"):
                return spec.version
        return None
