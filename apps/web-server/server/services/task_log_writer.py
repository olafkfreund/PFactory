"""
Task log writer.

Persists detailed per-phase execution logs to ``task_logs.json`` and streams
them to open task-detail views via WebSocket. Extracted verbatim from
``agent_service.py`` (issue Factory#255 seam b); ``agent_service`` re-exports
``TaskLogWriter`` so existing import paths keep working.

The per-line ``noqa`` directives mark pre-existing violations carried over
verbatim from ``agent_service.py`` (behavior-preserving move; legacy is fixed
on touch per the ratchet policy, not during extraction).
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ..websockets.events import emit_task_logs_stream  # noqa: TID252
from .task_models import TaskPhase


class TaskLogWriter:
    """Writes detailed phase logs to task_logs.json."""

    # Tool patterns for Claude Code CLI output
    TOOL_PATTERNS = [  # noqa: RUF012
        # Pattern: "⏺ ToolName" or emoji + tool name
        (r'[⏺🔧📖✏️📝🔍💻]\s*(Read|Write|Edit|Bash|Glob|Grep|Task|WebFetch|WebSearch|LSP|NotebookEdit)\b', 'tool_start'),  # noqa: E501
        # Pattern: "Tool: ToolName" format
        (r'^Tool:\s*(Read|Write|Edit|Bash|Glob|Grep|Task|WebFetch|WebSearch|LSP|NotebookEdit)\b', 'tool_start'),  # noqa: E501
        # Pattern: Claude Code verbose format "Using Read tool"
        (r'Using\s+(Read|Write|Edit|Bash|Glob|Grep|Task|WebFetch|WebSearch|LSP|NotebookEdit)\s+tool', 'tool_start'),  # noqa: E501
        # Pattern: Tool invocation with parameters like "Read(file_path=...)"
        (r'^(Read|Write|Edit|Bash|Glob|Grep|Task|WebFetch|WebSearch|LSP|NotebookEdit)\s*\(', 'tool_start'),  # noqa: E501
    ]

    # Phase mapping from TaskPhase to task_logs.json phases
    # Note: COMPLETED and FAILED are NOT mapped here - they represent task
    # completion states, not execution phases. Use _get_current_phase() to
    # determine which phase the task was actually in when it completed/failed.
    PHASE_MAP = {  # noqa: RUF012
        TaskPhase.SPEC_CREATION: "planning",
        TaskPhase.PLANNING: "planning",
        TaskPhase.PLAN_REVIEW: "planning",
        TaskPhase.CODING: "coding",
        TaskPhase.QA_REVIEW: "validation",
        TaskPhase.QA_FIXING: "validation",
    }

    def __init__(self, spec_dir: Path):
        self.spec_dir = spec_dir
        self.log_file = spec_dir / "task_logs.json"
        self._current_tool: str | None = None
        self._tool_start_time: str | None = None
        self._tool_input: str | None = None
        self._pending_tool_output: list[str] = []
        self._initialized = False
        # Throttling for text emission (avoid flooding WebSocket)
        self._last_text_emit_time: float = 0
        self._text_emit_interval: float = 1.0  # seconds
        self._pending_text_lines: list[str] = []

    def _ensure_initialized(self, spec_id: str) -> dict[str, Any]:
        """Ensure task_logs.json exists with proper structure."""
        if self.log_file.exists():
            try:
                with open(self.log_file) as f:  # noqa: PTH123
                    return cast(dict[str, Any], json.load(f))
            except (OSError, json.JSONDecodeError):
                pass

        # Create new structure
        now = datetime.now().isoformat()  # noqa: DTZ005
        return {
            "spec_id": spec_id,
            "created_at": now,
            "updated_at": now,
            "phases": {
                "planning": {
                    "phase": "planning",
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "entries": []
                },
                "coding": {
                    "phase": "coding",
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "entries": []
                },
                "validation": {
                    "phase": "validation",
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "entries": []
                }
            }
        }

    def _save(self, data: dict[str, Any]) -> None:
        """Save task_logs.json."""
        self.spec_dir.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = datetime.now().isoformat()  # noqa: DTZ005
        with open(self.log_file, 'w') as f:  # noqa: PTH123
            json.dump(data, f, indent=2)

    def _detect_tool(self, line: str) -> tuple[str, str] | None:
        """Detect tool invocation in a line. Returns (tool_name, tool_input) or None."""
        for pattern, _ in self.TOOL_PATTERNS:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                tool_name = match.group(1)
                # Try to extract input after tool name
                input_match = re.search(rf'{tool_name}\s*\(([^)]*)\)', line)
                tool_input = input_match.group(1) if input_match else ""
                # Also check for file paths or other context
                if not tool_input:
                    path_match = re.search(r'["\']([^"\']+)["\']', line)
                    if path_match:
                        tool_input = path_match.group(1)
                return (tool_name, tool_input[:200] if tool_input else "")
        return None

    def _maybe_emit_text(self, spec_id: str, phase: TaskPhase) -> None:
        """Emit accumulated text if enough time has passed (throttled)."""
        import time  # noqa: PLC0415
        now = time.time()
        if now - self._last_text_emit_time >= self._text_emit_interval:
            self._flush_pending_text(spec_id, phase)

    def _flush_pending_text(self, spec_id: str, phase: TaskPhase) -> None:
        """Flush accumulated text lines as a single entry."""
        import time  # noqa: PLC0415
        if self._pending_text_lines:
            # Take last 20 lines to avoid huge entries
            content = "\n".join(self._pending_text_lines[-20:])
            self.add_entry(spec_id, phase, "text", content)
            self._pending_text_lines = []
            self._last_text_emit_time = time.time()

    def add_entry(self, spec_id: str, phase: TaskPhase, entry_type: str,  # noqa: PLR0913
                  content: str, tool_name: str | None = None,
                  tool_input: str | None = None, detail: str | None = None,
                  subphase: str | None = None) -> None:
        """Add a log entry to the appropriate phase."""
        data = self._ensure_initialized(spec_id)
        phase_key = self.PHASE_MAP.get(phase, "coding")

        entry = {
            "timestamp": datetime.now().isoformat(),  # noqa: DTZ005
            "type": entry_type,
            "content": content,
        }

        if tool_name:
            entry["tool_name"] = tool_name
        if tool_input:
            entry["tool_input"] = tool_input
        if detail:
            entry["detail"] = detail[:5000]  # Limit detail size
        if subphase:
            entry["subphase"] = subphase

        data["phases"][phase_key]["entries"].append(entry)

        # Update phase status
        if data["phases"][phase_key]["status"] == "pending":
            data["phases"][phase_key]["status"] = "active"
            data["phases"][phase_key]["started_at"] = datetime.now().isoformat()  # noqa: DTZ005

        self._save(data)

        # Emit WebSocket event for real-time streaming to open task detail modals
        # Format as TaskLogStreamChunk to match frontend interface
        stream_chunk: dict[str, Any] = {
            "type": entry_type,
            "content": content,
            "phase": phase_key,
            "timestamp": entry["timestamp"],
        }
        # Add tool info if present
        if tool_name:
            stream_chunk["tool"] = {"name": tool_name}
            if tool_input:
                stream_chunk["tool"]["input"] = tool_input
        # Add subtask info if present (from subphase)
        if subphase:
            stream_chunk["subtask_id"] = subphase

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(emit_task_logs_stream(spec_id, stream_chunk))  # noqa: RUF006
        except RuntimeError:
            # No event loop running, skip WebSocket emit
            pass

    def process_line(self, spec_id: str, phase: TaskPhase, line: str) -> None:
        """Process a line of output and detect tool usage."""
        if not line.strip():
            return

        # Check for tool invocation
        tool_info = self._detect_tool(line)

        if tool_info:
            # Flush pending text before starting a new tool
            self._flush_pending_text(spec_id, phase)

            # If there was a previous tool, close it
            if self._current_tool:
                self.add_entry(
                    spec_id, phase, "tool_end",
                    f"Completed {self._current_tool}",
                    tool_name=self._current_tool,
                    detail="\n".join(self._pending_tool_output[-50:]) if self._pending_tool_output else None  # noqa: E501
                )

            # Start new tool
            tool_name, tool_input = tool_info
            self._current_tool = tool_name
            self._tool_start_time = datetime.now().isoformat()  # noqa: DTZ005
            self._tool_input = tool_input
            self._pending_tool_output = []

            self.add_entry(
                spec_id, phase, "tool_start",
                f"Using {tool_name}",
                tool_name=tool_name,
                tool_input=tool_input
            )
        elif self._current_tool:
            # Accumulate output for current tool
            self._pending_tool_output.append(line)

            # Check for tool completion patterns
            if any(p in line.lower() for p in ['done', 'completed', 'success', 'error', 'failed']):
                # Might be end of tool, but don't close yet - let next tool close it
                pass
        else:
            # Not in a tool context - accumulate text and emit periodically
            self._pending_text_lines.append(line)
            self._maybe_emit_text(spec_id, phase)

    def set_phase_status(self, spec_id: str, phase: TaskPhase, status: str) -> None:
        """Update phase status (active, completed, failed)."""
        data = self._ensure_initialized(spec_id)
        phase_key = self.PHASE_MAP.get(phase, "coding")

        data["phases"][phase_key]["status"] = status

        if status == "active" and not data["phases"][phase_key]["started_at"]:
            data["phases"][phase_key]["started_at"] = datetime.now().isoformat()  # noqa: DTZ005
        elif status in ("completed", "failed"):
            data["phases"][phase_key]["completed_at"] = datetime.now().isoformat()  # noqa: DTZ005

            # Flush any pending text
            self._flush_pending_text(spec_id, phase)

            # Close any pending tool
            if self._current_tool:
                self.add_entry(
                    spec_id, phase, "tool_end",
                    f"Completed {self._current_tool}",
                    tool_name=self._current_tool,
                    detail="\n".join(self._pending_tool_output[-50:]) if self._pending_tool_output else None  # noqa: E501
                )
                self._current_tool = None
                self._pending_tool_output = []

        self._save(data)

    def finalize(self, spec_id: str, phase: TaskPhase) -> None:
        """Finalize logging - close any pending tools and flush text."""
        # Flush any pending text first
        self._flush_pending_text(spec_id, phase)

        if self._current_tool:
            self.add_entry(
                spec_id, phase, "tool_end",
                f"Completed {self._current_tool}",
                tool_name=self._current_tool,
                detail="\n".join(self._pending_tool_output[-50:]) if self._pending_tool_output else None  # noqa: E501
            )
            self._current_tool = None
            self._pending_tool_output = []
