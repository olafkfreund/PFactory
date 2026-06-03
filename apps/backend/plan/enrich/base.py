"""Infra adapter framework — read-only live infrastructure introspection.

Shared contract for the Kubernetes (#8), OpenShift (#9), and cloud (#10)
adapters. An adapter answers two questions: *can I reach this environment?*
(:meth:`InfraAdapter.available`) and *what's running, read-only?*
(:meth:`InfraAdapter.discover`). Discovery returns an :class:`InfraSnapshot`,
which the Enrich stage appends to ``NormalizedPlan.enrichment.infra``.

**Read-only is a hard contract.** Adapters MUST only issue list/get/describe
calls. No create/update/delete/apply/scale — ever. The planner uses this context
to make plans realistic (capacity, quotas, policies); it never changes the
target.
"""

from __future__ import annotations

import abc

from pydantic import BaseModel, Field


class InfraSnapshot(BaseModel):
    """A read-only snapshot of one infrastructure target.

    Adapters fill what they can; unknown fields stay empty. ``raw`` may hold
    adapter-specific detail. Shapes inside the lists are adapter-defined but
    should be small JSON-able dicts (e.g. a workload's name/namespace/limits).
    """

    adapter: str
    target: str = ""  # cluster context / account id / subscription / project
    available: bool = True
    workloads: list[dict] = Field(default_factory=list)
    resources: dict = Field(default_factory=dict)  # quotas, limits, capacity
    policies: list[dict] = Field(default_factory=list)  # network/SCC/org policy
    load: dict = Field(default_factory=dict)  # live utilisation if available
    findings: list[str] = Field(default_factory=list)  # human-readable notes
    raw: dict = Field(default_factory=dict)
    error: str | None = None


class InfraAdapter(abc.ABC):
    """Base class for a read-only infrastructure adapter.

    Subclasses set ``name`` and implement :meth:`available` and :meth:`discover`.
    Construction must not perform network I/O; defer that to ``discover()``.
    """

    name: str = "base"

    def __init__(self, *, target: str = "", **options: object) -> None:
        self.target = target
        self.options = options

    @abc.abstractmethod
    def available(self) -> bool:
        """True if credentials/config to reach the target are present."""

    @abc.abstractmethod
    def discover(self) -> InfraSnapshot:
        """Return a read-only snapshot of the target environment."""

    def to_enrichment(self) -> dict:
        """Discover and return a JSON-able dict for ``enrichment.infra``.

        Never raises: a failed discovery is captured in the snapshot's
        ``error`` / ``available`` fields so enrichment degrades gracefully.
        """
        try:
            if not self.available():
                return InfraSnapshot(
                    adapter=self.name, target=self.target, available=False,
                    error="adapter not available (missing config/credentials)",
                ).model_dump()
            return self.discover().model_dump()
        except Exception as exc:  # never let enrichment break the pipeline
            return InfraSnapshot(
                adapter=self.name, target=self.target, available=False,
                error=f"{type(exc).__name__}: {exc}",
            ).model_dump()


# ── registry ───────────────────────────────────────────────────────────

_REGISTRY: dict[str, type[InfraAdapter]] = {}


def register_adapter(cls: type[InfraAdapter]) -> type[InfraAdapter]:
    """Class decorator registering an adapter under its ``name``."""
    _REGISTRY[cls.name] = cls
    return cls


def available_adapters() -> list[str]:
    """Names of all registered infra adapters."""
    return sorted(_REGISTRY)


def get_adapter(name: str, **kwargs: object) -> InfraAdapter:
    """Instantiate a registered adapter by name."""
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown infra adapter '{name}'; registered: {available_adapters()}"
        ) from None
    return cls(**kwargs)
