"""Board snapshots survive a move from a pod with spec files to one without them."""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from sqlalchemy import event, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.database.models import Base, TaskBoardState  # noqa: E402
from server.routes import projects, tasks  # noqa: E402
from server.services import task_log_store  # noqa: E402

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
    def loader() -> dict:
        return {"proj": {"path": str(path)}}

    monkeypatch.setattr(tasks, "load_projects", loader)
    monkeypatch.setattr(projects, "load_projects", loader)


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
    store: async_sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[type-arg]
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
    store: async_sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[type-arg]
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
            queries = []

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
    store: async_sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[type-arg]
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
