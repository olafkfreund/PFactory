"""#807: two pods consuming the same OAuth connect state -- exactly one wins.

The state is single-use: a replayed or duplicated provider callback must not
connect a mailbox twice. On Postgres the consume is one DELETE ... RETURNING,
so concurrent consumers are serialized by the row lock.
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

from server.routes import email as email_routes  # noqa: E402
from tests.postgres.helpers import reset_schema, run_alembic  # noqa: E402


def test_concurrent_consumes_of_one_state_have_one_winner(
    test_postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_schema(test_postgres_url)  # see helpers.reset_schema
    result = run_alembic(["upgrade", "head"], env={"DATABASE_URL": test_postgres_url})
    assert result.returncode == 0, f"alembic upgrade head failed: {result.stderr[-1000:]}"

    async def _go() -> list[dict[str, str | None] | None]:
        engine = create_async_engine(test_postgres_url, pool_size=4)
        monkeypatch.setattr(
            email_routes,
            "async_session_factory",
            async_sessionmaker(engine, expire_on_commit=False),
        )
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM oauth_connect_states"))
        state = await email_routes._save_connect_state("user-1", "outlook", None)
        results = await asyncio.gather(
            *(email_routes._consume_connect_state(state, "outlook") for _ in range(2))
        )
        await engine.dispose()
        return list(results)

    results = asyncio.run(_go())
    assert sum(r is not None for r in results) == 1, f"expected one winner, got {results}"
