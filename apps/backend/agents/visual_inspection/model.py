"""Visual Inspection Run data model (#170 / P1 #171).

The deterministic core shared by the packager, the report renderer, and (later)
the portal store. A run is a sequence of verification **steps**, each with a
pass/fail state, a labeled screenshot, and (on fail) an error — plus the
recording (video + trace). ``meta.json`` is the pinned contract between the P1
writer and the P4 portal reader (see the design spec).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

__all__ = [
    "StepResult",
    "RunMeta",
    "verdict_for",
    "slugify",
    "new_run_id",
    "build_meta",
]

_VERDICTS = ("pass", "attention", "fail")


def slugify(value: str) -> str:
    """A filesystem-safe step slug (``Open incident`` → ``open-incident``)."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return s or "step"


@dataclass(frozen=True)
class StepResult:
    """One verification step of the run."""

    n: int
    label: str
    state: str  # "pass" | "fail"
    screenshot: str | None = None  # path relative to the run dir (screenshots/…)
    error: str | None = None  # populated when state == "fail"

    def to_dict(self) -> dict:
        d: dict = {"n": self.n, "label": self.label, "state": self.state}
        if self.screenshot:
            d["screenshot"] = self.screenshot
        if self.error:
            d["error"] = self.error
        return d


def verdict_for(steps: list[StepResult]) -> str:
    """Overall verdict: any failed step → ``fail``; none → ``pass``.

    ``attention`` is reserved for non-fatal findings (e.g. a visual-baseline
    drift below threshold); P1 never auto-assigns it.
    """
    if not steps:
        return "attention"  # nothing verified is itself worth a human look
    return "fail" if any(s.state == "fail" for s in steps) else "pass"


@dataclass(frozen=True)
class RunMeta:
    """The machine-readable run summary written to ``meta.json``."""

    id: str
    target: dict  # {name, platform?, base_url?}
    created_at: str  # ISO 8601 (UTC)
    steps: list[StepResult]
    video: str | None = None  # path relative to the run dir
    trace: str | None = None
    verdict: str = field(default="")

    def to_dict(self) -> dict:
        passed = sum(1 for s in self.steps if s.state == "pass")
        failed = sum(1 for s in self.steps if s.state == "fail")
        return {
            "id": self.id,
            "target": self.target,
            "created_at": self.created_at,
            "verdict": self.verdict or verdict_for(self.steps),
            "counts": {"steps": len(self.steps), "passed": passed, "failed": failed},
            "steps": [s.to_dict() for s in self.steps],
            "recording": {"video": self.video, "trace": self.trace},
        }


def new_run_id(target_name: str, *, now: datetime.datetime | None = None) -> str:
    """Sortable, filesystem-safe run id: ``<target>-<UTC timestamp>``."""
    ts = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%d%H%M%S")
    return f"{slugify(target_name)}-{ts}"


def build_meta(
    *,
    run_id: str,
    target: dict,
    steps: list[StepResult],
    created_at: str,
    video: str | None = None,
    trace: str | None = None,
) -> RunMeta:
    """Assemble a ``RunMeta`` with its verdict computed from the steps."""
    return RunMeta(
        id=run_id,
        target=target,
        created_at=created_at,
        steps=list(steps),
        video=video,
        trace=trace,
        verdict=verdict_for(steps),
    )
