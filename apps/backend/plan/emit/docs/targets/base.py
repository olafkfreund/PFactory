"""The DocsTarget protocol — every documentation sink implements this.

Targets are duck-typed (a ``Protocol``) so the orchestrator + tests can swap
fakes without inheritance, matching the codebase's seam style.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..bundle import DocBundle, TargetResult


@runtime_checkable
class DocsTarget(Protocol):
    """A place a rendered plan can be published to."""

    #: stable id: "repo" | "backstage" | "confluence"
    name: str

    def available(self) -> bool:
        """True when this target is configured/usable (else it is skipped)."""
        ...

    def publish(self, bundle: DocBundle) -> TargetResult:
        """Publish the bundle. Must never raise — return a TargetResult."""
        ...
