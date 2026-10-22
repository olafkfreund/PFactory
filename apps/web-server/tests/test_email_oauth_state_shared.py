"""#807: the email OAuth connect state is shared by every replica.

The state used to live in a per-process dict, so a provider callback that
reached the other pod was rejected as "invalid or expired". Each test uses a
fresh session per step, standing in for the pod that happens to serve it.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.database.models import Base, OAuthConnectState  # noqa: E402
from server.routes import email as email_routes  # noqa: E402


@pytest.fixture
def shared_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[async_sessionmaker]:  # type: ignore[type-arg]
    nonce = secrets.token_hex(8)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:oauth807-{nonce}?mode=memory&cache=shared&uri=true"
    )

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(email_routes, "async_session_factory", session_local)
    yield session_local
    asyncio.run(engine.dispose())


@pytest.mark.usefixtures("shared_db")
def test_a_state_saved_on_one_pod_is_consumed_on_another() -> None:
    state = asyncio.run(
        email_routes._save_connect_state("user-1", "outlook", "https://portal.example")
    )
    got = asyncio.run(email_routes._consume_connect_state(state, "outlook"))
    assert got == {"user_id": "user-1", "origin": "https://portal.example"}


@pytest.mark.usefixtures("shared_db")
def test_a_state_is_single_use() -> None:
    state = asyncio.run(email_routes._save_connect_state("user-1", "gmail", None))
    assert asyncio.run(email_routes._consume_connect_state(state, "gmail")) is not None
    assert asyncio.run(email_routes._consume_connect_state(state, "gmail")) is None


@pytest.mark.usefixtures("shared_db")
def test_a_state_is_bound_to_its_provider() -> None:
    state = asyncio.run(email_routes._save_connect_state("user-1", "gmail", None))
    assert asyncio.run(email_routes._consume_connect_state(state, "outlook")) is None
    # The refused attempt did not burn it for the right provider.
    assert asyncio.run(email_routes._consume_connect_state(state, "gmail")) is not None


def test_an_expired_state_is_refused_and_swept(shared_db: async_sessionmaker) -> None:  # type: ignore[type-arg]
    async def _expire(state: str) -> None:
        async with shared_db() as session:
            row = await session.get(OAuthConnectState, state)
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    async def _count() -> int:
        async with shared_db() as session:
            return int(
                (
                    await session.execute(select(func.count()).select_from(OAuthConnectState))
                ).scalar_one()
            )

    stale = asyncio.run(email_routes._save_connect_state("user-1", "outlook", None))
    asyncio.run(_expire(stale))
    assert asyncio.run(email_routes._consume_connect_state(stale, "outlook")) is None

    kept = asyncio.run(email_routes._save_connect_state("user-2", "outlook", None))
    asyncio.run(_expire(kept))
    asyncio.run(email_routes._save_connect_state("user-3", "outlook", None))  # sweeps
    assert asyncio.run(_count()) == 1
