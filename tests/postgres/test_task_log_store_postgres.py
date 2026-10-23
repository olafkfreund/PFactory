"""#829: task logs in real Postgres after ``alembic upgrade head``.

Two writers stand in for two pods appending to one task; the reader has no
filesystem access to either. Then the age cap removes a backdated row.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

pytest.importorskip("asyncpg")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.services import task_log_store  # noqa: E402
from server.services.task_log_writer import TaskLogWriter  # noqa: E402
from server.services.task_models import TaskPhase  # noqa: E402
from tests.postgres.helpers import reset_schema, run_alembic  # noqa: E402

TASK = "proj:001-feature"
SPEC = "001-feature"


def test_two_pods_write_one_reader_reads_and_prune_caps_age(
    test_postgres_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reset_schema(test_postgres_url)
    result = run_alembic(["upgrade", "head"], env={"DATABASE_URL": test_postgres_url})
    assert result.returncode == 0, f"alembic upgrade head failed: {result.stderr[-1000:]}"
    monkeypatch.setenv("PFACTORY_TASK_LOG_RETENTION_DAYS", "30")

    async def _go() -> tuple[dict | None, dict | None]:
        engine = create_async_engine(test_postgres_url)
        monkeypatch.setattr(
            task_log_store,
            "async_session_factory",
            async_sessionmaker(engine, expire_on_commit=False),
        )
        monkeypatch.setattr(task_log_store, "enabled", lambda: True)
        a = TaskLogWriter(tmp_path / "pod-a", task_id=TASK)
        b = TaskLogWriter(tmp_path / "pod-b", task_id=TASK)
        a.set_phase_status(SPEC, TaskPhase.CODING, "active")
        for i in range(20):
            a.add_entry(SPEC, TaskPhase.CODING, "text", f"a{i}")
            b.add_entry(SPEC, TaskPhase.CODING, "text", f"b{i}")
            await asyncio.sleep(0)
        await asyncio.gather(a.flush_db(), b.flush_db())
        before = await task_log_store.load(TASK)

        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE task_log_entries SET created_at = now() - interval '31 days';")
            )
            await conn.execute(
                text("UPDATE task_log_phases SET updated_at = now() - interval '31 days';")
            )
        await task_log_store.prune()
        after = await task_log_store.load(TASK)
        await engine.dispose()
        return before, after

    before, after = asyncio.run(_go())
    assert before is not None
    got = [e["content"] for e in before["phases"]["coding"]["entries"]]
    assert [c for c in got if c[0] == "a"] == [f"a{i}" for i in range(20)]
    assert [c for c in got if c[0] == "b"] == [f"b{i}" for i in range(20)]
    assert before["phases"]["coding"]["status"] == "active"
    assert after is None
