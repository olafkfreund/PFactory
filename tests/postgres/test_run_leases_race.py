"""#805: many replicas racing to start the same run -- exactly one wins.

``acquire`` is one INSERT ... ON CONFLICT DO UPDATE ... WHERE, so the loser's
statement sees the winner's row and matches nothing.
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

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.services import run_leases  # noqa: E402
from tests.postgres.helpers import reset_schema, run_alembic  # noqa: E402

RACERS = 8


def test_concurrent_acquires_of_one_key_have_one_winner(
    test_postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_schema(test_postgres_url)
    result = run_alembic(["upgrade", "head"], env={"DATABASE_URL": test_postgres_url})
    assert result.returncode == 0, f"alembic upgrade head failed: {result.stderr[-1000:]}"

    async def _go() -> list[bool]:
        engine = create_async_engine(test_postgres_url, pool_size=RACERS + 2)
        monkeypatch.setattr(
            run_leases, "async_session_factory", async_sessionmaker(engine, expire_on_commit=False)
        )

        async def racer(i: int) -> bool:
            # Each racer is a different pod.
            return await run_leases.acquire("task", "proj:001", owner=f"pod-{i}:1")

        results = await asyncio.gather(*(racer(i) for i in range(RACERS)))
        await engine.dispose()
        return list(results)

    results = asyncio.run(_go())
    assert results.count(True) == 1, f"expected exactly one winner, got {results}"
