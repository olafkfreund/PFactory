"""Pure task-model types extracted from agent_service (issue #255, seam 1 of 5).

Contains the task-model cluster that is safe to import without pulling in
the full AgentService dependency tree: enums, dataclasses, pure helper
functions, and the dedup-signature utility. All names are re-exported from
:mod:`server.services.agent_service` so every historical import path keeps
working unchanged.

Do not import anything from this package here — this module must remain a
leaf node with no intra-package dependencies so it can be imported cheaply
by the websocket handlers and tests without booting the full service stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TaskPhase(str, Enum):
    """Task execution phases."""

    SPEC_CREATION = "spec_creation"
    PLANNING = "planning"
    PLAN_REVIEW = "plan_review"  # Paused for human plan approval
    CODING = "coding"
    QA_REVIEW = "qa_review"
    QA_FIXING = "qa_fixing"
    COMPLETED = "completed"
    FAILED = "failed"


def phase_to_status(phase: TaskPhase) -> str:
    """Map execution phase to task status for kanban column placement."""
    mapping = {
        TaskPhase.SPEC_CREATION: "in_progress",
        TaskPhase.PLANNING: "in_progress",
        TaskPhase.PLAN_REVIEW: "human_review",  # Paused for human plan approval
        TaskPhase.CODING: "in_progress",
        TaskPhase.QA_REVIEW: "ai_review",
        TaskPhase.QA_FIXING: "in_progress",
        TaskPhase.COMPLETED: "human_review",
        TaskPhase.FAILED: "human_review",
    }
    return mapping.get(phase, "in_progress")


def phase_to_review_reason(phase: TaskPhase) -> str | None:
    """Map execution phase to reviewReason field value.

    Returns the appropriate reviewReason for phases that result in human_review status:
    - PLAN_REVIEW: "plan_review" (waiting for plan approval before coding)
    - COMPLETED: "completed" (task finished successfully, needs final approval)
    - FAILED: "errors" (task failed, needs human intervention)

    Returns None for phases that don't require a reviewReason.
    """
    mapping = {
        TaskPhase.PLAN_REVIEW: "plan_review",
        TaskPhase.COMPLETED: "completed",
        TaskPhase.FAILED: "errors",
    }
    return mapping.get(phase)


# Phase ranges for overall progress scaling (start%, end%)
# Maps within-phase progress (0-100) to an overall range so progress is monotonically increasing.
PHASE_RANGES: dict[str, tuple[float, float]] = {
    "spec_creation": (0, 20),
    "planning": (0, 20),
    "plan_review": (20, 20),  # Fixed at 20%
    "coding": (20, 80),
    "qa_review": (80, 95),
    "qa_fixing": (80, 95),
    "completed": (95, 100),
    "failed": (0, 0),  # Keep whatever was last
}


def scale_progress(phase: str, phase_progress: float) -> float:
    """Scale within-phase progress (0-100) to overall progress range.

    Example: coding phase at 50% -> 20 + (50/100) x 60 = 50% overall.
    """
    start, end = PHASE_RANGES.get(phase, (0, 100))
    width = end - start
    return round(start + (phase_progress / 100) * width)


@dataclass
class TaskProgress:
    """Real-time task progress information."""

    task_id: str
    phase: TaskPhase
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())  # noqa: DTZ005
    subtask: str | None = None
    subtask_index: int | None = None
    subtask_total: int | None = None
    percentage: float | None = None
    overall_progress: float | None = None  # Override scaled overall progress
    sequence_number: int = 0  # For frontend out-of-order detection
    started_at: str | None = None  # Task start time for UI display
    data: dict = field(default_factory=dict)


@dataclass
class TaskLog:
    """A single log entry from task execution."""

    task_id: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())  # noqa: DTZ005
    level: str = "info"  # info, warning, error, debug
    source: str = "agent"  # agent, stdout, stderr


def _dedup_signature(payload: dict) -> tuple:
    """Compute a structural signature of a task:update payload for deduplication.

    Excluded (volatile per-tick, not material to state):
      - message            — streams free-text per tick during QA, etc.
      - sequenceNumber     — monotonically increases on every emit by design
      - startedAt          — fixed for a task's lifetime
      - timestamp          — wall-clock per emit

    Included (material state):
      - phase / executionProgress.{phase, phaseProgress, overallProgress, currentSubtask}
      - subtasksCompleted / subtasksTotal
      - subtasks (as a tuple of (id, status) pairs — checkbox transitions are
        meaningful even when phase/progress haven't moved)
    """
    exec_ = payload.get("executionProgress") or {}
    subtasks = payload.get("subtasks") or []
    return (
        payload.get("phase"),
        exec_.get("phase"),
        exec_.get("phaseProgress"),
        exec_.get("overallProgress"),
        exec_.get("currentSubtask"),
        payload.get("subtasksCompleted"),
        payload.get("subtasksTotal"),
        tuple((s.get("id"), s.get("status")) for s in subtasks),
    )
