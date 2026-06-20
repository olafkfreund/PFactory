"""Liveness watchdog (#95) — flag a silent in-flight stage as ``stalled``.

A stage that hangs (the documented "Stream closed" failure) leaves
``status.json`` in an active "-ing" state with a frozen ``updated_at``: the
status claims an agent is working, but no further writes ever happen, so the
task hangs forever and the portal shows it "stuck". This module turns that
frozen timestamp into an explicit ``stalled`` status a watcher (or the portal)
can act on.

Pure compute + a best-effort writer:
  - ``evaluate_liveness`` reads ``status.json`` and compares ``updated_at``
    against an injected ``now`` — no side effects, fully unit-testable.
  - ``mark_stalled`` flips the status (best-effort) and emits a #95 stage
    event so a watcher learns immediately.
  - ``check_and_mark`` is the convenience the periodic driver calls.

Only the four *active* statuses can stall — handoff states
(planned/generated/evaluated) and terminal/failed states are deliberately
excluded so an already-settled task is never clobbered. The periodic driver
that calls this on a timer (web-server loop / cron) is the caller's concern.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ACTIVE_STATUSES",
    "StallVerdict",
    "check_and_mark",
    "evaluate_liveness",
    "mark_stalled",
]

# Only these "an agent is actively running" statuses can stall. Handoff states
# (planned/generated/evaluated/replan_needed) and terminal/failed states are
# excluded on purpose — flipping them would risk false positives (auto-fire may
# be off, or the task is already settled).
ACTIVE_STATUSES = frozenset({"planning", "generating", "evaluating", "triaging"})

# Which agent owns each active status — used to label the emitted stage event.
_STATUS_TO_STAGE = {
    "planning": "planner",
    "generating": "gen_functional",
    "evaluating": "evaluator",
    "triaging": "triager",
}

# Default idle budget before an active stage is considered stalled. Generous —
# real stages (LLM calls, 3x stability re-runs, Docker) can be legitimately
# quiet for a while. Override with PFACTORY_STALL_DEADLINE_SECONDS.
DEFAULT_DEADLINE_SECONDS = 900  # 15 min


def _deadline_seconds() -> float:
    raw = os.environ.get("PFACTORY_STALL_DEADLINE_SECONDS")
    if raw is None:
        return float(DEFAULT_DEADLINE_SECONDS)
    try:
        return float(raw)
    except ValueError:
        return float(DEFAULT_DEADLINE_SECONDS)


def _now_iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds")


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Our writers are tz-aware, but treat a naive timestamp as UTC just in case.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@dataclass(frozen=True)
class StallVerdict:
    """The watchdog's read of one task. ``stalled`` drives the flip."""

    stalled: bool
    status: str | None  # the status observed (None when unreadable)
    phase: str | None
    idle_seconds: float | None  # None when updated_at is missing/unparseable
    reason: str

    @property
    def ok(self) -> bool:
        """True when the task is NOT stalled (healthy or not-applicable)."""
        return not self.stalled


def evaluate_liveness(
    spec_dir: Path,
    *,
    now: datetime,
    deadline_seconds: float | None = None,
) -> StallVerdict:
    """Decide whether the task at ``spec_dir`` has stalled — no side effects.

    Stalled ⇔ ``status`` is in :data:`ACTIVE_STATUSES` AND ``status.json``'s
    ``updated_at`` is older than ``deadline_seconds`` before ``now``.

    A missing/corrupt ``status.json``, a non-active status, or an unparseable
    ``updated_at`` all yield ``stalled=False`` — fail-safe; never flip on
    ambiguous input.
    """
    deadline = _deadline_seconds() if deadline_seconds is None else deadline_seconds
    status_path = spec_dir / "status.json"
    if not status_path.exists():
        return StallVerdict(False, None, None, None, "no status.json")
    try:
        status = json.loads(status_path.read_text())
    except (json.JSONDecodeError, OSError):
        return StallVerdict(False, None, None, None, "status.json unreadable")
    if not isinstance(status, dict):
        return StallVerdict(False, None, None, None, "status.json not an object")

    st = status.get("status")
    phase = status.get("phase")
    if st not in ACTIVE_STATUSES:
        return StallVerdict(False, st, phase, None, f"status '{st}' is not active")

    updated = _parse_iso(status.get("updated_at"))
    if updated is None:
        return StallVerdict(False, st, phase, None, "updated_at missing/unparseable")

    idle = (now - updated).total_seconds()
    if idle > deadline:
        return StallVerdict(
            True,
            st,
            phase,
            idle,
            f"active '{st}' idle {idle:.0f}s > {deadline:.0f}s deadline",
        )
    return StallVerdict(False, st, phase, idle, f"active '{st}' idle {idle:.0f}s")


def mark_stalled(spec_dir: Path, verdict: StallVerdict, *, now: datetime) -> bool:
    """Flip the task to ``status='stalled'`` (best-effort). Returns success.

    Re-reads ``status.json`` and only flips if it is *still* in an active
    status — so a stage that finished between ``evaluate_liveness`` and here is
    never clobbered. Preserves the prior status as ``stalled_from`` and emits a
    #95 stage event so a watcher learns immediately.
    """
    if not verdict.stalled:
        return False
    status_path = spec_dir / "status.json"
    try:
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(status, dict) or status.get("status") not in ACTIVE_STATUSES:
        # The stage advanced since the verdict was computed — leave it alone.
        return False

    prior = status.get("status")
    status["stalled_from"] = prior
    status["status"] = "stalled"
    status["phase"] = "watchdog_stalled"
    status["stall_idle_seconds"] = round(verdict.idle_seconds or 0.0)
    status["updated_at"] = _now_iso(now)
    try:
        status_path.write_text(json.dumps(status, indent=2))
    except OSError:
        return False

    # Surface immediately to any #95 watcher (best-effort; never raises).
    try:
        from agents.stage_events import emit_stage_event

        emit_stage_event(spec_dir, status, stage=_STATUS_TO_STAGE.get(prior or "", "unknown"))
    except Exception:
        pass
    return True


def check_and_mark(
    spec_dir: Path,
    *,
    now: datetime | None = None,
    deadline_seconds: float | None = None,
) -> StallVerdict:
    """Evaluate liveness and flip to ``stalled`` if needed. Driver convenience.

    Returns the :class:`StallVerdict` so the caller can log/act. ``now``
    defaults to the current UTC time.
    """
    when = now or datetime.now(UTC)
    verdict = evaluate_liveness(spec_dir, now=when, deadline_seconds=deadline_seconds)
    if verdict.stalled:
        mark_stalled(spec_dir, verdict, now=when)
    return verdict
