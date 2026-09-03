"""Value objects for the docs emit: the rendered bundle + a per-target result."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DocBundle:
    """One deterministic render of a plan, shared by every target.

    ``markdown`` is the human page; ``registry_entry`` is the machine index row
    (the cross-factory memory record, keyed by ``correlation_key``).
    """

    plan_id: str
    slug: str  # filename stem, e.g. "006-fastapi-gateway"
    title: str
    correlation_key: str
    content_hash: str
    markdown: str
    registry_entry: dict[str, Any]
    # The run signature (#700): who/what/when produced THIS render, plus the
    # review verdict it was produced from. Written beside the page as
    # ``<slug>.run.json`` so a checkout can answer "which planner run wrote
    # this, against which text, and did it pass?" without a server round-trip.
    run_signature: dict[str, Any] = field(default_factory=dict)


@dataclass
class TargetResult:
    """Outcome of publishing a bundle to one target. Never raised — recorded."""

    target: str  # "repo" | "backstage" | "confluence"
    status: str  # "written" | "skipped" | "error"
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"target": self.target, "status": self.status, "detail": self.detail}
