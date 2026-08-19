"""
Data models for task logging.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class LogPhase(str, Enum):
    """Log phases matching the execution flow."""

    PLANNING = "planning"
    CODING = "coding"
    VALIDATION = "validation"


class LogEntryType(str, Enum):
    """Types of log entries."""

    TEXT = "text"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"
    ERROR = "error"
    SUCCESS = "success"
    INFO = "info"


@dataclass
class LogEntry:
    """A single log entry."""

    timestamp: str
    type: str
    content: str
    phase: str
    tool_name: str | None = None
    tool_input: str | None = None
    subtask_id: str | None = None
    session: int | None = None
    # New fields for expandable detail view
    detail: str | None = (
        None  # Full content that can be expanded (e.g., file contents, command output)
    )
    subphase: str | None = (
        None  # Subphase grouping (e.g., "PROJECT DISCOVERY", "CONTEXT GATHERING")
    )
    collapsed: bool | None = None  # Whether to show collapsed by default in UI

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class PhaseLog:
    """Logs for a single phase."""

    phase: str
    status: str  # "pending", "active", "completed", "failed"
    started_at: str | None = None
    completed_at: str | None = None
    entries: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "entries": self.entries,
        }
