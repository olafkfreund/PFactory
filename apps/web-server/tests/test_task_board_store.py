"""Board snapshots survive a move from a pod with spec files to one without them."""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from sqlalchemy import event, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.database.models import Base, TaskBoardState  # noqa: E402
from server.routes import execution, projects, tasks  # noqa: E402
from server.services import project_paths, run_leases, task_log_store  # noqa: E402
from server.services.agent_service import AgentService  # noqa: E402
from server.services.task_models import TaskPhase, TaskProgress  # noqa: E402

TASK = "proj:001-feature"
SPEC = "001-feature"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[async_sessionmaker]:  # type: ignore[type-arg]
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:taskboard905-{secrets.token_hex(8)}?mode=memory&cache=shared&uri=true"
    )

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(init())
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_log_store, "async_session_factory", session_local)
    monkeypatch.setattr(task_log_store, "enabled", lambda: True)
    yield session_local
    asyncio.run(engine.dispose())


def project(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    def loader() -> dict[str, dict[str, str]]:
        return {"proj": {"path": str(path)}}

    monkeypatch.setattr(tasks, "load_projects", loader)
    monkeypatch.setattr(projects, "load_projects", loader)
    monkeypatch.setattr(project_paths, "load_projects", loader)


def make_spec(root: Path, name: str = SPEC) -> Path:
    spec = root / ".pfactory" / "specs" / name
    spec.mkdir(parents=True)
    (spec / "requirements.json").write_text(json.dumps({"title": "Feature", "description": "Work"}))
    plan = {
        "status": "in_progress",
        "phase": "coding",
        "archivedAt": "2026-10-23",
        "phases": [{"subtasks": [{"id": "1", "description": "Build", "status": "completed"}]}],
    }
    (spec / "test_plan.json").write_text(json.dumps(plan))
    logs = {"phases": {"coding": {"status": "active", "started_at": "2026-10-23", "entries": []}}}
    (spec / "task_logs.json").write_text(json.dumps(logs))
    return spec


@pytest.mark.usefixtures("store")
def test_remote_routes_and_local_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "a"
    remote = tmp_path / "b"
    remote.mkdir()
    spec = make_spec(local)

    async def go() -> None:
        project(monkeypatch, local)
        expected = await projects.list_project_tasks("proj")
        assert len(expected) == 1
        await tasks.update_task_status(TASK, tasks.TaskStatusUpdate(status="in_progress"))
        await asyncio.sleep(0.05)
        assert await task_log_store.load_board(TASK) is not None
        project(monkeypatch, remote)
        got = await projects.list_project_tasks("proj")
        assert len(got) == 1
        for field in ("status", "phase", "subtasks", "executionProgress"):
            assert got[0][field] == expected[0][field]
        assert got[0]["specsPath"] is None
        detail = await tasks.get_task(TASK)
        assert detail["executionProgress"] == got[0]["executionProgress"]
        assert detail["metadata"]["archivedAt"] == "2026-10-23"
        listing = await tasks.list_tasks(project_id="proj", status=None)
        assert listing.total == 1
        assert listing.tasks[0].status == "in_progress"
        assert listing.tasks[0].phase == "coding"
        assert listing.tasks[0].subtasks[0].status == "completed"
        project(monkeypatch, local)
        (spec / "test_plan.json").write_text(json.dumps({"status": "done"}))
        assert (await projects.list_project_tasks("proj"))[0]["status"] == "done"

    asyncio.run(go())


def test_store_off_uses_files_without_writes(
    store: async_sessionmaker,  # type: ignore[type-arg]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_spec(tmp_path)
    project(monkeypatch, tmp_path)
    monkeypatch.setattr(task_log_store, "enabled", lambda: False)

    async def go() -> None:
        assert len(await projects.list_project_tasks("proj")) == 1
        assert (await tasks.list_tasks(project_id="proj", status=None)).total == 1
        await tasks.update_task_status(TASK, tasks.TaskStatusUpdate(status="human_review"))
        async with store() as session:
            assert (await session.scalar(select(func.count()).select_from(TaskBoardState))) == 0

    asyncio.run(go())


def test_project_list_uses_one_query_for_remote_tasks(
    store: async_sessionmaker,  # type: ignore[type-arg]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project(monkeypatch, tmp_path)

    async def go() -> None:
        total = 0
        for count in (1, 5):
            total += count
            for i in range(count):
                spec = make_spec(tmp_path / f"writer{count}", f"{count}{i:02d}-feature")
                await task_log_store.save_board(
                    f"proj:{spec.name}", "proj", tasks.board_record("proj", spec)
                )
            queries: list[str] = []

            def count_query(
                _conn: object,
                _cursor: object,
                statement: str,
                *_args: object,
                sink: list[str] = queries,
            ) -> None:
                sink.append(statement)

            engine = store.kw["bind"]
            event.listen(engine.sync_engine, "before_cursor_execute", count_query)
            try:
                assert len(await projects.list_project_tasks("proj")) == total
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", count_query)
            assert len(queries) == 1

    asyncio.run(go())


def test_delete_and_prune_board_rows(
    store: async_sessionmaker,  # type: ignore[type-arg]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def go() -> None:
        spec = make_spec(tmp_path)
        record = tasks.board_record("proj", spec)
        await task_log_store.save_board(TASK, "proj", record)
        await task_log_store.delete(TASK)
        assert await task_log_store.load_board(TASK) is None
        await task_log_store.save_board(TASK, "proj", record)
        async with store() as session:
            row = await session.get(TaskBoardState, TASK)
            row.updated_at = datetime.now(UTC) - timedelta(days=100)
            await session.commit()
        monkeypatch.setenv("PFACTORY_TASK_LOG_RETENTION_DAYS", "90")
        await task_log_store.prune()
        assert await task_log_store.load_board(TASK) is None

    asyncio.run(go())


def end_logs(spec: Path) -> None:
    logs = {"phases": {"coding": {"status": "failed", "started_at": "2026-10-23", "entries": []}}}
    (spec / "task_logs.json").write_text(json.dumps(logs))


class _StoppedProc:
    def terminate(self) -> None:
        pass

    async def wait(self) -> int:
        return -15


@pytest.mark.usefixtures("store")
def test_stop_and_interrupted_runs_reach_remote_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "a"
    remote = tmp_path / "b"
    remote.mkdir()

    async def remote_status() -> str:
        project(monkeypatch, remote)
        try:
            return str((await projects.list_project_tasks("proj"))[0]["status"])
        finally:
            project(monkeypatch, local)

    async def go() -> None:
        spec = make_spec(local)
        project(monkeypatch, local)
        await task_log_store.save_board(TASK, "proj", tasks.board_record("proj", spec))
        end_logs(spec)  # what the run's log writers do on stop
        service = AgentService()
        service.running_tasks[TASK] = _StoppedProc()  # type: ignore[assignment]
        service._spec_dirs[TASK] = spec
        assert await service.stop_task(TASK)
        await asyncio.sleep(0.05)
        expected = (await projects.list_project_tasks("proj"))[0]["status"]
        assert expected != "in_progress"
        assert await remote_status() == expected

        # Pod exit: the row must be written before the cancelled monitor returns.
        other = make_spec(local, "002-other")
        task_id = f"proj:{other.name}"
        await task_log_store.save_board(task_id, "proj", tasks.board_record("proj", other))
        end_logs(other)
        await AgentService()._fail_interrupted_run(task_id, local, other.name)
        record = await task_log_store.load_board(task_id)
        assert record is not None
        assert record["task"]["status"] != "in_progress"

    asyncio.run(go())


@pytest.mark.usefixtures("store")
def test_created_task_reaches_remote_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = tmp_path / "a"
    remote = tmp_path / "b"
    remote.mkdir()
    (local / ".pfactory" / "specs").mkdir(parents=True)

    async def go() -> None:
        project(monkeypatch, local)
        created = await tasks.create_task(
            tasks.TaskCreate(project_id="proj", title="New", description="Work")
        )
        await asyncio.sleep(0.05)
        project(monkeypatch, remote)
        assert [t.id for t in (await tasks.list_tasks(project_id="proj", status=None)).tasks] == [
            created.id
        ]

    asyncio.run(go())


async def _settle() -> None:
    # Wait for the spawned board flush: reading while it writes races on the one test connection.
    for _ in range(500):
        await asyncio.sleep(0.01)
        if not task_log_store._board_flushers:
            return


async def _remote_board(monkeypatch: pytest.MonkeyPatch, local: Path, remote: Path) -> Any:
    await _settle()
    project(monkeypatch, remote)
    try:
        return await projects.list_project_tasks("proj")
    finally:
        project(monkeypatch, local)


@pytest.mark.usefixtures("store")
def test_progress_event_reaches_remote_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "a"
    remote = tmp_path / "b"
    remote.mkdir()

    async def go() -> None:
        spec = make_spec(local)
        project(monkeypatch, local)
        await task_log_store.save_board(TASK, "proj", tasks.board_record("proj", spec))
        # What the running agent writes mid-run: a new phase and subtask progress.
        plan = json.loads((spec / "test_plan.json").read_text())
        plan["phase"] = "qa_review"
        plan["phases"][0]["subtasks"].append(
            {"id": "2", "description": "Test", "status": "in_progress"}
        )
        (spec / "test_plan.json").write_text(json.dumps(plan))
        expected = (await projects.list_project_tasks("proj"))[0]
        service = AgentService()
        service._spec_dirs[TASK] = spec
        await service._emit_progress(
            TaskProgress(task_id=TASK, phase=TaskPhase.QA_REVIEW, message="qa")
        )
        got = (await _remote_board(monkeypatch, local, remote))[0]
        assert len(got["subtasks"]) == 2
        for field in ("status", "phase", "subtasks", "executionProgress"):
            assert got[field] == expected[field]

    asyncio.run(go())


@pytest.mark.usefixtures("store")
def test_archive_and_recover_reach_remote_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "a"
    remote = tmp_path / "b"
    remote.mkdir()

    async def inactive(*_args: object) -> bool:
        return False

    monkeypatch.setattr(run_leases, "is_active", inactive)

    async def go() -> None:
        spec = make_spec(local)
        project(monkeypatch, local)
        await task_log_store.save_board(TASK, "proj", tasks.board_record("proj", spec))

        await projects.unarchive_tasks("proj", projects.UnarchiveTasksRequest(taskIds=[TASK]))
        assert "archivedAt" not in (await _remote_board(monkeypatch, local, remote))[0].get(
            "metadata", {}
        )
        await projects.archive_tasks("proj", projects.ArchiveTasksRequest(taskIds=[TASK]))
        assert (
            (await _remote_board(monkeypatch, local, remote))[0]
            .get("metadata", {})
            .get("archivedAt")
        )

        end_logs(spec)  # a dead run: no active phase left
        before = (await _remote_board(monkeypatch, local, remote))[0]["status"]
        await execution.recover_task(TASK)
        await _settle()
        expected = (await projects.list_project_tasks("proj"))[0]["status"]
        assert expected != before
        assert (await _remote_board(monkeypatch, local, remote))[0]["status"] == expected

    asyncio.run(go())


@pytest.mark.usefixtures("store")
def test_plan_review_hold_reaches_remote_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "a"
    remote = tmp_path / "b"
    remote.mkdir()

    class Idle:
        def is_running(self, _task_id: str) -> bool:
            return False

        async def is_running_anywhere(self, _task_id: str) -> bool:
            return False

    async def no_emit(*_args: object) -> None:
        return None

    monkeypatch.setattr(execution, "get_agent_service", Idle)
    monkeypatch.setattr(execution, "emit_task_status", no_emit)

    async def go() -> None:
        spec = make_spec(local)
        (spec / "task_metadata.json").write_text(json.dumps({"requireReviewBeforeCoding": True}))
        end_logs(spec)  # not started yet: no active phase
        project(monkeypatch, local)
        monkeypatch.setattr(execution, "resolve_project_path", lambda _pid: local)
        await task_log_store.save_board(TASK, "proj", tasks.board_record("proj", spec))

        result = await execution.start_task(TASK, execution.StartTaskRequest(), object())  # type: ignore[arg-type]
        assert result["status"] == "human_review"
        await _settle()
        local_view = (await projects.list_project_tasks("proj"))[0]
        remote_view = (await _remote_board(monkeypatch, local, remote))[0]
        assert local_view["status"] == "human_review"
        assert remote_view["status"] == "human_review"
        assert remote_view.get("reviewReason") == local_view.get("reviewReason")

    asyncio.run(go())


@pytest.mark.usefixtures("store")
def test_worktree_sync_reaches_remote_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "a"
    remote = tmp_path / "b"
    remote.mkdir()

    async def go() -> None:
        spec = make_spec(local)
        project(monkeypatch, local)
        plan = json.loads((spec / "test_plan.json").read_text())
        plan["phases"][0]["subtasks"][0]["status"] = "pending"
        (spec / "test_plan.json").write_text(json.dumps(plan))
        await task_log_store.save_board(TASK, "proj", tasks.board_record("proj", spec))

        worktree = local / ".pfactory" / "worktrees" / "tasks" / SPEC / ".pfactory" / "specs" / SPEC
        worktree.mkdir(parents=True)
        plan["phases"][0]["subtasks"][0]["status"] = "completed"
        (worktree / "test_plan.json").write_text(json.dumps(plan))

        await AgentService()._sync_worktree_files(local, SPEC, TASK)
        local_view = (await projects.list_project_tasks("proj"))[0]
        remote_view = (await _remote_board(monkeypatch, local, remote))[0]
        assert local_view["subtasks"][0]["status"] == "completed"
        assert remote_view["subtasks"] == local_view["subtasks"]

    asyncio.run(go())
