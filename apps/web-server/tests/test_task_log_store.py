"""#829: task logs live in the shared store, so any replica can read them.

Two "pods" are two TaskLogWriter instances with different spec directories; the
reader is the logs route with a project path that holds neither directory.
"""

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

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.database.models import Base, TaskLogEntry, TaskLogPhase  # noqa: E402
from server.routes import tasks as tasks_routes  # noqa: E402
from server.services import task_log_store  # noqa: E402
from server.services.task_log_writer import TaskLogWriter  # noqa: E402
from server.services.task_models import TaskPhase  # noqa: E402

TASK = "proj:001-feature"
SPEC = "001-feature"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[async_sessionmaker]:  # type: ignore[type-arg]
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:tasklog829-{secrets.token_hex(8)}?mode=memory&cache=shared&uri=true"
    )

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_log_store, "async_session_factory", session_local)
    monkeypatch.setattr(task_log_store, "enabled", lambda: True)
    yield session_local
    asyncio.run(engine.dispose())


def _project(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(tasks_routes, "load_projects", lambda: {"proj": {"path": str(path)}})


def _contents(phase: dict[str, Any]) -> list[str]:
    return [e["content"] for e in phase["entries"]]


@pytest.mark.usefixtures("store")
def test_log_written_on_pod_a_is_read_on_pod_b(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pod_a = tmp_path / "pod-a"
    pod_b = tmp_path / "pod-b"  # holds no spec directory and no task_logs.json
    pod_b.mkdir()

    async def _go() -> dict[str, Any]:
        writer = TaskLogWriter(pod_a / "spec", task_id=TASK)
        writer.set_phase_status(SPEC, TaskPhase.PLANNING, "active")
        writer.add_entry(SPEC, TaskPhase.PLANNING, "text", "planning line")
        writer.set_phase_status(SPEC, TaskPhase.PLANNING, "completed")
        writer.add_entry(SPEC, TaskPhase.CODING, "text", "coding line")
        await writer.flush_db()
        _project(monkeypatch, pod_b)
        logs: dict[str, Any] = await tasks_routes.get_task_logs(TASK)
        return logs

    result = asyncio.run(_go())
    phases = result["phases"]
    assert _contents(phases["planning"]) == ["planning line"]
    assert phases["planning"]["status"] == "completed"
    assert phases["planning"]["completed_at"]
    assert _contents(phases["coding"]) == ["coding line"]
    assert phases["coding"]["status"] == "active"
    assert phases["validation"]["status"] == "pending"
    assert result["specId"] == SPEC
    # The owning pod still has its local file.
    assert (pod_a / "spec" / "task_logs.json").exists()


@pytest.mark.usefixtures("store")
def test_concurrent_writers_keep_their_append_order(
    tmp_path: Path,
) -> None:
    n = 40

    async def _go() -> dict[str, Any] | None:
        a = TaskLogWriter(tmp_path / "a", task_id=TASK)
        b = TaskLogWriter(tmp_path / "b", task_id=TASK)
        for i in range(n):
            a.add_entry(SPEC, TaskPhase.CODING, "text", f"a{i}")
            b.add_entry(SPEC, TaskPhase.CODING, "text", f"b{i}")
            if i % 7 == 0:
                await asyncio.sleep(0)  # let both flushers run mid-stream
        await asyncio.gather(a.flush_db(), b.flush_db())
        return await task_log_store.load(TASK)

    doc = asyncio.run(_go())
    assert doc is not None
    got = _contents(doc["phases"]["coding"])
    assert len(got) == 2 * n
    assert [c for c in got if c.startswith("a")] == [f"a{i}" for i in range(n)]
    assert [c for c in got if c.startswith("b")] == [f"b{i}" for i in range(n)]


def test_without_shared_store_the_file_is_used(
    store: async_sessionmaker,  # type: ignore[type-arg]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(task_log_store, "enabled", lambda: False)
    spec_dir = tmp_path / ".pfactory" / "specs" / SPEC

    async def _go() -> tuple[dict[str, Any], int]:
        writer = TaskLogWriter(spec_dir, task_id=TASK)
        writer.add_entry(SPEC, TaskPhase.CODING, "text", "file only")
        await writer.flush_db()
        _project(monkeypatch, tmp_path)
        result = await tasks_routes.get_task_logs(TASK)
        async with store() as session:
            rows = (await session.execute(select(func.count()).select_from(TaskLogEntry))).scalar()
        return result, int(rows or 0)

    result, rows = asyncio.run(_go())
    assert rows == 0
    assert _contents(result["phases"]["coding"]) == ["file only"]


@pytest.mark.usefixtures("store")
def test_no_db_rows_falls_back_to_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A task that was running before the store existed: only the file has its log.
    spec_dir = tmp_path / ".pfactory" / "specs" / SPEC
    spec_dir.mkdir(parents=True)
    doc = {
        "spec_id": SPEC,
        "phases": {
            "coding": {"phase": "coding", "status": "active", "entries": [{"content": "old"}]}
        },
    }
    (spec_dir / "task_logs.json").write_text(json.dumps(doc))
    _project(monkeypatch, tmp_path)

    result = asyncio.run(tasks_routes.get_task_logs(TASK))
    assert _contents(result["phases"]["coding"]) == ["old"]


def test_prune_deletes_rows_past_the_retention_cap(
    store: async_sessionmaker,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PFACTORY_TASK_LOG_RETENTION_DAYS", "30")
    old = datetime.now(UTC) - timedelta(days=31)

    async def _go() -> tuple[list[str], list[str]]:
        async with store() as session:
            session.add_all(
                [
                    TaskLogEntry(
                        task_id="p:old", phase="coding", entry={"content": "x"}, created_at=old
                    ),
                    TaskLogPhase(
                        task_id="p:old", phase="coding", status="completed", updated_at=old
                    ),
                    TaskLogEntry(task_id="p:new", phase="coding", entry={"content": "y"}),
                    TaskLogPhase(task_id="p:new", phase="coding", status="active"),
                ]
            )
            await session.commit()
        await task_log_store.prune()
        async with store() as session:
            entries = (await session.execute(select(TaskLogEntry.task_id))).scalars().all()
            phases = (await session.execute(select(TaskLogPhase.task_id))).scalars().all()
        return list(entries), list(phases)

    entries, phases = asyncio.run(_go())
    assert entries == ["p:new"]
    assert phases == ["p:new"]


def test_zero_retention_disables_prune(
    store: async_sessionmaker,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PFACTORY_TASK_LOG_RETENTION_DAYS", "0")
    old = datetime.now(UTC) - timedelta(days=3650)

    async def _go() -> int:
        async with store() as session:
            session.add(TaskLogEntry(task_id="p:old", phase="coding", entry={}, created_at=old))
            await session.commit()
        await task_log_store.prune()
        async with store() as session:
            return int(
                (await session.execute(select(func.count()).select_from(TaskLogEntry))).scalar()
                or 0
            )

    assert asyncio.run(_go()) == 1


@pytest.mark.usefixtures("store")
def test_delete_task_removes_its_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_dir = tmp_path / ".pfactory" / "specs" / SPEC

    async def _go() -> dict[str, Any] | None:
        writer = TaskLogWriter(spec_dir, task_id=TASK)
        writer.add_entry(SPEC, TaskPhase.CODING, "text", "line")
        await writer.flush_db()
        _project(monkeypatch, tmp_path)
        await tasks_routes.delete_task(TASK)
        return await task_log_store.load(TASK)

    assert asyncio.run(_go()) is None
