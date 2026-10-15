"""Derive per-lane progress from the task status on read (#861, TFactory#1259).

The stored ``lane_progress`` is only written at planning time, so it stays
``pending`` once the task moves on. Nothing here writes ``status.json``.
"""

import json
from pathlib import Path

from test_plan.enums import _V01_LANE_ALIASES

_EVALUATED = {"evaluated", "triaging", "triaged", "triaged_empty", "triager_failed"}
_PASSTHROUGH = {
    "planner_failed",
    "gen_functional_failed",
    "evaluator_failed",
    "stalled",
    "planned_empty",
    "generated_empty",
    "evaluated_empty",
}


def _plan_lanes(spec_dir: Path) -> set[str]:
    try:
        plan = json.loads((spec_dir / "test_plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(plan, dict) or not isinstance(plan.get("phases"), list):
        return set()
    lanes = set()
    for phase in plan["phases"]:
        subtasks = phase.get("subtasks") if isinstance(phase, dict) else None
        for sub in subtasks if isinstance(subtasks, list) else []:
            lane = sub.get("lane") if isinstance(sub, dict) else None
            if isinstance(lane, str) and lane.strip():
                name = lane.strip().lower()
                alias = _V01_LANE_ALIASES.get(name)  # old plans: functional/sast/... -> unit
                lanes.add(alias.value if alias else name)
    return lanes


def derive_lane_progress(spec_dir: Path, status: dict) -> dict[str, str] | None:
    """Return the stored lane_progress with derived lanes, or None to keep it."""
    task_status = status.get("status")
    if task_status == "evaluating":
        value = "evaluating"
    elif task_status in _EVALUATED:
        value = "evaluated"
    elif task_status in _PASSTHROUGH:
        value = task_status
    else:
        return None
    stored = status.get("lane_progress")
    stored = dict(stored) if isinstance(stored, dict) else {}
    lanes = _plan_lanes(spec_dir) or set(stored)
    if not lanes:
        return None
    return {**stored, **dict.fromkeys(lanes, value)}
