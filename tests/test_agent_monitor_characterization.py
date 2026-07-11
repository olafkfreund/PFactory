"""
Characterization tests for the AgentService process-monitor cluster.

Pins down the observable behavior of ``_parse_phase_event``,
``_process_output``, and ``_monitor_process`` BEFORE the cluster is moved
into ``AgentProcessMonitorMixin`` (Factory#255 seam e). These tests assert
current behavior, not desired behavior — they must pass unchanged before
and after the extraction.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# Add web-server source root to path so we can import the service module
_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
sys.path.insert(0, str(_WEB_SERVER))

from server.services.agent_service import AgentService, TaskPhase, TaskProgress  # noqa: E402


class FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process."""

    def __init__(self, returncode: int = 0) -> None:
        self._rc = returncode
        self.terminated = False

    async def wait(self) -> int:
        return self._rc

    def terminate(self) -> None:
        self.terminated = True


def _make_stream(lines: list[str]) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data(line.encode("utf-8") + b"\n")
    reader.feed_eof()
    return reader


# ---------------------------------------------------------------------------
# _parse_phase_event — pure parsing
# ---------------------------------------------------------------------------


class TestParsePhaseEvent:
    def setup_method(self) -> None:
        self.service = AgentService()

    def test_exec_phase_json_maps_progress_to_percentage(self) -> None:
        line = '__EXEC_PHASE__:{"phase":"coding","message":"Starting","progress":50}'
        event = self.service._parse_phase_event(line)
        assert event == {"phase": "coding", "message": "Starting", "percentage": 50}

    def test_exec_phase_invalid_json_returns_none(self) -> None:
        assert self.service._parse_phase_event("__EXEC_PHASE__:{not json") is None

    def test_phase_event_key_value_format(self) -> None:
        event = self.service._parse_phase_event("[PHASE_EVENT] phase=coding message=hello")
        assert event == {"phase": "coding", "message": "hello"}

    def test_plain_line_returns_none(self) -> None:
        assert self.service._parse_phase_event("just some agent output") is None


# ---------------------------------------------------------------------------
# _process_output — stream-driven phase tracking
# ---------------------------------------------------------------------------


class TestProcessOutput:
    def setup_method(self) -> None:
        self.service = AgentService()

    @pytest.mark.asyncio
    async def test_phase_event_updates_current_phase_and_returns_it(self) -> None:
        task_id = "proj:spec-po-1"
        stream = _make_stream(
            [
                "plain output line",
                '__EXEC_PHASE__:{"phase":"coding","message":"go","progress":10}',
            ]
        )
        seen: list[TaskProgress] = []
        self.service.register_progress_callback(task_id, seen.append)

        with (
            patch("server.services.agent_service.emit_task_update", new=AsyncMock()),
            patch("server.services.agent_service.emit_task_status", new=AsyncMock()),
        ):
            final = await self.service._process_output(task_id, stream)

        assert final == TaskPhase.CODING
        assert self.service._task_current_phases[task_id] == TaskPhase.CODING
        assert [p.phase for p in seen] == [TaskPhase.CODING]
        assert seen[0].message == "go"
        assert seen[0].percentage == 10

    @pytest.mark.asyncio
    async def test_rate_limit_line_sets_flag(self) -> None:
        task_id = "proj:spec-po-2"
        stream = _make_stream(["You've hit your limit, try again later"])

        with (
            patch("server.services.agent_service.emit_task_update", new=AsyncMock()),
            patch("server.services.agent_service.emit_task_status", new=AsyncMock()),
        ):
            await self.service._process_output(task_id, stream)

        assert self.service._task_rate_limits[task_id] is True

    @pytest.mark.asyncio
    async def test_emits_logs_to_registered_callback(self) -> None:
        task_id = "proj:spec-po-3"
        stream = _make_stream(["line one", "line two"])
        logs: list[Any] = []
        self.service.register_log_callback(task_id, logs.append)

        with (
            patch("server.services.agent_service.emit_task_update", new=AsyncMock()),
            patch("server.services.agent_service.emit_task_status", new=AsyncMock()),
        ):
            await self.service._process_output(task_id, stream)

        assert [log.content for log in logs] == ["line one", "line two"]
        assert all(log.source == "stdout" for log in logs)


# ---------------------------------------------------------------------------
# _monitor_process — terminal emissions + tracking cleanup
# ---------------------------------------------------------------------------


class TestMonitorProcess:
    def setup_method(self) -> None:
        self.service = AgentService()

    async def _run_monitor(self, task_id: str, returncode: int) -> list[TaskProgress]:
        proc = FakeProc(returncode=returncode)
        self.service.running_tasks[task_id] = proc  # type: ignore[assignment]
        self.service._task_current_phases[task_id] = TaskPhase.CODING
        seen: list[TaskProgress] = []
        self.service.register_progress_callback(task_id, seen.append)

        with (
            patch("server.services.agent_service.emit_task_update", new=AsyncMock()),
            patch("server.services.agent_service.emit_task_status", new=AsyncMock()),
        ):
            await self.service._monitor_process(task_id, proc)  # type: ignore[arg-type]
        return seen

    @pytest.mark.asyncio
    async def test_exit_zero_emits_completed_and_cleans_tracking(self) -> None:
        task_id = "proj:spec-mon-ok"
        seen = await self._run_monitor(task_id, returncode=0)

        assert [p.phase for p in seen] == [TaskPhase.COMPLETED]
        assert seen[0].overall_progress == 100
        assert task_id not in self.service.running_tasks
        assert task_id not in self.service._task_current_phases
        assert task_id not in self.service._task_rate_limits

    @pytest.mark.asyncio
    async def test_nonzero_exit_emits_failed_with_exit_code(self) -> None:
        task_id = "proj:spec-mon-fail"
        seen = await self._run_monitor(task_id, returncode=3)

        assert [p.phase for p in seen] == [TaskPhase.FAILED]
        assert "exit code 3" in seen[0].message
        assert task_id not in self.service.running_tasks

    @pytest.mark.asyncio
    async def test_user_stopped_task_skips_terminal_emission(self) -> None:
        task_id = "proj:spec-mon-stopped"
        self.service._task_stopped.add(task_id)
        proc = FakeProc(returncode=1)
        self.service.running_tasks[task_id] = proc  # type: ignore[assignment]
        seen: list[TaskProgress] = []
        self.service.register_progress_callback(task_id, seen.append)

        with (
            patch("server.services.agent_service.emit_task_update", new=AsyncMock()),
            patch("server.services.agent_service.emit_task_status", new=AsyncMock()),
        ):
            await self.service._monitor_process(task_id, proc)  # type: ignore[arg-type]

        assert seen == []
        assert task_id not in self.service._task_stopped
