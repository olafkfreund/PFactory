"""An interrupted run monitor must persist a failed end, not leave in_progress (#863)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_WEB_SERVER = Path(__file__).parent.parent / "apps" / "web-server"
sys.path.insert(0, str(_WEB_SERVER))

from server.routes.tasks import load_spec_metadata  # noqa: E402
from server.services.agent_service import AgentService, TaskPhase  # noqa: E402
from server.services.task_log_writer import TaskLogWriter  # noqa: E402

TASK_ID = "proj:001-feature"
SPEC_ID = "001-feature"


class HangingProc:
    """A process that never exits while monitored."""

    returncode = None

    async def wait(self) -> int:
        await asyncio.Event().wait()
        return 0


def _service(tmp_path: Path) -> tuple[AgentService, Path]:
    spec_dir = tmp_path / ".pfactory" / "specs" / SPEC_ID
    spec_dir.mkdir(parents=True)
    service = AgentService()
    writers = (TaskLogWriter(spec_dir), TaskLogWriter(spec_dir))
    writers[0].set_phase_status(SPEC_ID, TaskPhase.CODING, "active")
    service._task_log_writers[TASK_ID] = writers
    service._task_current_phases[TASK_ID] = TaskPhase.CODING
    service._update_plan_status = AsyncMock()
    service._sync_worktree_files = AsyncMock()
    return service, spec_dir


async def _cancel_monitor(service: AgentService, project_path: Path) -> None:
    task = asyncio.create_task(
        service._monitor_process(TASK_ID, HangingProc(), project_path, SPEC_ID)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _coding_status(spec_dir: Path) -> str:
    return json.loads((spec_dir / "task_logs.json").read_text())["phases"]["coding"]["status"]


@pytest.mark.asyncio
async def test_cancelled_monitor_persists_failed(tmp_path: Path) -> None:
    service, spec_dir = _service(tmp_path)
    assert load_spec_metadata(spec_dir)["status"] == "in_progress"

    await _cancel_monitor(service, tmp_path)

    assert _coding_status(spec_dir) == "failed"
    assert load_spec_metadata(spec_dir)["status"] != "in_progress"
    service._update_plan_status.assert_awaited_once_with(
        tmp_path, SPEC_ID, "failed", TASK_ID, emit_events=False
    )


@pytest.mark.asyncio
async def test_user_stopped_task_is_left_to_stop_task(tmp_path: Path) -> None:
    service, spec_dir = _service(tmp_path)
    service._task_stopped.add(TASK_ID)

    await _cancel_monitor(service, tmp_path)

    assert _coding_status(spec_dir) == "active"
    service._update_plan_status.assert_not_awaited()


class DoneProc:
    """A process that has already exited successfully."""

    returncode = 0

    async def wait(self) -> int:
        return 0


async def _hang(*_a: object, **_k: object) -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_cancel_after_completed_write_keeps_completed(tmp_path: Path) -> None:
    service, spec_dir = _service(tmp_path)
    service._emit_progress = AsyncMock(side_effect=_hang)  # cancel lands mid-emission
    task = asyncio.create_task(service._monitor_process(TASK_ID, DoneProc(), tmp_path, SPEC_ID))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if service._emit_progress.await_count:
            break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    statuses = [c.args[2] for c in service._update_plan_status.await_args_list]
    assert statuses == ["completed"]
    assert _coding_status(spec_dir) == "completed"


@pytest.mark.asyncio
async def test_crash_after_completed_write_keeps_completed(tmp_path: Path) -> None:
    service, spec_dir = _service(tmp_path)
    service._emit_progress = AsyncMock(side_effect=RuntimeError("ws down"))
    # The crash branch's own FAILED emission raises too (unchanged behaviour).
    with pytest.raises(RuntimeError):
        await service._monitor_process(TASK_ID, DoneProc(), tmp_path, SPEC_ID)

    statuses = [c.args[2] for c in service._update_plan_status.await_args_list]
    assert statuses == ["completed"]
    assert _coding_status(spec_dir) == "completed"
