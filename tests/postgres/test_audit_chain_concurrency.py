"""#806: concurrent audit writers produce ONE linear hash chain on Postgres.

Before #806 the chain head was read with no lock, so writers in separate
sessions (two requests, or two replicas) linked to the same head and forked the
chain. Each writer here sleeps between its insert and its commit, which is
exactly the window the advisory lock has to cover.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

_WEB = Path(__file__).parent.parent.parent / "apps" / "web-server"
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

pytest.importorskip("asyncpg")

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.database.models import AuditLog  # noqa: E402
from server.services.audit_chain import row_as_mapping, verify_chain  # noqa: E402
from server.services.audit_service import log_audit_event  # noqa: E402
from tests.postgres.helpers import run_alembic  # noqa: E402

WRITERS = 8


@pytest.fixture
def migrated_url(test_postgres_url: str) -> str:
    result = run_alembic(["upgrade", "head"], env={"DATABASE_URL": test_postgres_url})
    assert result.returncode == 0, f"alembic upgrade head failed: {result.stderr[-1000:]}"

    async def _empty() -> None:
        engine = create_async_engine(test_postgres_url)
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM audit_logs"))
        await engine.dispose()

    asyncio.run(_empty())
    return test_postgres_url


async def _write(url: str, count: int, *, concurrent: bool) -> list[dict]:
    engine = create_async_engine(url, pool_size=WRITERS + 2)
    session_local = async_sessionmaker(engine, expire_on_commit=False)

    async def writer(i: int) -> None:
        async with session_local() as session:
            await log_audit_event(db=session, action=f"test.race.{i}", resource_type="test")
            await asyncio.sleep(0.05)  # hold the transaction open past the insert
            await session.commit()

    if concurrent:
        await asyncio.gather(*(writer(i) for i in range(count)))
    else:
        for i in range(count):
            await writer(i)

    async with session_local() as session:
        rows = (await session.execute(select(AuditLog).order_by(AuditLog.chain_seq))).scalars()
        out = [{**row_as_mapping(r), "chain_seq": r.chain_seq} for r in rows]
    await engine.dispose()
    return out


def _assert_linear(rows: list[dict], expected: int) -> None:
    assert len(rows) == expected, f"expected {expected} rows, got {len(rows)} (a writer was lost)"
    assert [r["chain_seq"] for r in rows] == list(range(1, expected + 1))
    ok, bad, reason = verify_chain(rows)
    assert ok, f"chain forked at row {bad}: {reason}"


def test_concurrent_writers_on_an_empty_chain(migrated_url: str) -> None:
    rows = asyncio.run(_write(migrated_url, WRITERS, concurrent=True))
    _assert_linear(rows, WRITERS)


def test_concurrent_writers_on_an_existing_chain(migrated_url: str) -> None:
    asyncio.run(_write(migrated_url, 3, concurrent=False))
    rows = asyncio.run(_write(migrated_url, WRITERS, concurrent=True))
    _assert_linear(rows, WRITERS + 3)


def test_the_migration_numbers_existing_rows_in_created_at_order(migrated_url: str) -> None:
    env = {"DATABASE_URL": migrated_url}
    down = run_alembic(["downgrade", "d4a7e2b9f1c6"], env=env)
    assert down.returncode == 0, f"downgrade failed: {down.stderr[-1000:]}"

    # Inserted out of time order, with a created_at tie broken by id.
    seed = [
        ("c", datetime(2026, 1, 3)),
        ("a", datetime(2026, 1, 1)),
        ("b2", datetime(2026, 1, 2)),
        ("b1", datetime(2026, 1, 2)),
    ]

    async def _seed() -> None:
        engine = create_async_engine(migrated_url)
        async with engine.begin() as conn:
            for row_id, ts in seed:
                await conn.execute(
                    text(
                        "INSERT INTO audit_logs (id, action, resource_type, created_at)"
                        " VALUES (:id, 'seed', 'test', :ts)"
                    ),
                    {"id": row_id, "ts": ts},
                )
        await engine.dispose()

    asyncio.run(_seed())
    up = run_alembic(["upgrade", "head"], env=env)
    assert up.returncode == 0, f"upgrade failed: {up.stderr[-1000:]}"

    async def _order() -> list[tuple[str, int]]:
        engine = create_async_engine(migrated_url)
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT id, chain_seq FROM audit_logs ORDER BY chain_seq")
            )
            out = [(r[0], r[1]) for r in result]
        await engine.dispose()
        return out

    assert asyncio.run(_order()) == [("a", 1), ("b1", 2), ("b2", 3), ("c", 4)]

    # Round trip: the downgrade drops the column and the upgrade re-numbers.
    assert run_alembic(["downgrade", "-1"], env=env).returncode == 0
    assert run_alembic(["upgrade", "head"], env=env).returncode == 0
    assert asyncio.run(_order()) == [("a", 1), ("b1", 2), ("b2", 3), ("c", 4)]
