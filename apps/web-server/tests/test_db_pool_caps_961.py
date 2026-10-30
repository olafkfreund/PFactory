"""Pool caps for the main database and the two shared stores (#961)."""

import asyncio

import pytest

from server.db_pool import pool_kwargs
from server.jobstore.plan_session_store import PlanSessionStore
from server.jobstore.store import JobStateStore

POSTGRES_URL = "postgresql+asyncpg://u:p@127.0.0.1:1/db"


@pytest.fixture(autouse=True)
def clear_pool_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for prefix in ("DB", "JOBSTORE_DB", "PLAN_SESSION_DB"):
        monkeypatch.delenv(f"{prefix}_POOL_SIZE", raising=False)
        monkeypatch.delenv(f"{prefix}_MAX_OVERFLOW", raising=False)
    monkeypatch.delenv("DB_POOL_TIMEOUT", raising=False)


def test_pool_kwargs_defaults() -> None:
    assert pool_kwargs(POSTGRES_URL, "DB", 5, 3) == {
        "pool_size": 5,
        "max_overflow": 3,
        "pool_timeout": 10,
    }


def test_pool_kwargs_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "4")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "12")
    assert pool_kwargs(POSTGRES_URL, "DB", 5, 3) == {
        "pool_size": 7,
        "max_overflow": 4,
        "pool_timeout": 12,
    }


def test_pool_kwargs_sqlite() -> None:
    assert pool_kwargs("sqlite+aiosqlite:///test.db", "DB", 5, 3) == {}


@pytest.mark.parametrize("name", ["DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT"])
def test_pool_kwargs_rejects_non_integer_env(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "not-an-integer")
    with pytest.raises(ValueError):
        pool_kwargs(POSTGRES_URL, "DB", 5, 3)


@pytest.mark.parametrize("store", [JobStateStore, PlanSessionStore])
def test_store_engine_pool_caps(store: type[JobStateStore] | type[PlanSessionStore]) -> None:
    async def check() -> None:
        engine = await store._make_engine_coro(POSTGRES_URL)
        try:
            assert engine.pool.size() == 2
            assert engine.pool._max_overflow == 2
            assert engine.pool._timeout == 10
        finally:
            await engine.dispose()

    asyncio.run(check())
