"""#805: the run-lease helper -- one owner per running thing, across replicas.

Two "pods" are simulated by patching ``run_leases.OWNER``. The lease TTL and
heartbeat are shrunk through their env vars so the watcher tests take about a
second each.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_WEB_SERVER = Path(__file__).resolve().parents[1]
if str(_WEB_SERVER) not in sys.path:
    sys.path.insert(0, str(_WEB_SERVER))

from sqlalchemy import select, text, update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from server.database.models import Base, RunLease  # noqa: E402
from server.services import run_leases  # noqa: E402

POD_A = "pod-a:1"
POD_B = "pod-b:1"


@pytest.fixture
def leases(monkeypatch: pytest.MonkeyPatch) -> Iterator[async_sessionmaker]:  # type: ignore[type-arg]
    nonce = secrets.token_hex(8)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:lease805-{nonce}?mode=memory&cache=shared&uri=true"
    )

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(run_leases, "async_session_factory", session_local)
    monkeypatch.setattr(run_leases, "OWNER", POD_A)
    monkeypatch.setenv("PFACTORY_RUN_LEASE_TTL_SECONDS", "3")
    monkeypatch.setenv("PFACTORY_RUN_LEASE_HEARTBEAT_SECONDS", "0.3")
    run_leases._WATCHERS.clear()
    yield session_local
    asyncio.run(engine.dispose())


def _as(owner: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_leases, "OWNER", owner)


async def _owner_of(session_local: async_sessionmaker, key: str) -> str | None:  # type: ignore[type-arg]
    async with session_local() as session:
        row: str | None = (
            await session.execute(select(RunLease.owner).where(RunLease.key == key))
        ).scalar_one_or_none()
        return row


@pytest.mark.usefixtures("leases")
def test_acquire_is_exclusive_and_reentrant_for_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def go() -> None:
        assert await run_leases.acquire("task", "p:1") is True
        assert await run_leases.acquire("task", "p:1") is True  # same pod restarting
        _as(POD_B, monkeypatch)
        assert await run_leases.acquire("task", "p:1") is False
        assert await run_leases.is_active("task", "p:1") is True
        assert await run_leases.active_keys("task") == ["p:1"]

    asyncio.run(go())


def test_an_expired_lease_can_be_taken(
    leases: async_sessionmaker,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def go() -> None:
        assert await run_leases.acquire("task", "p:1") is True
        async with leases() as session:
            await session.execute(
                update(RunLease).values(lease_until=text("datetime('now', '-1 seconds')"))
            )
            await session.commit()
        assert await run_leases.is_active("task", "p:1") is False
        _as(POD_B, monkeypatch)
        assert await run_leases.acquire("task", "p:1") is True
        assert await _owner_of(leases, "p:1") == POD_B

    asyncio.run(go())


def test_steal_takes_a_live_lease(
    leases: async_sessionmaker,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def go() -> None:
        assert await run_leases.acquire("insights", "p") is True
        _as(POD_B, monkeypatch)
        assert await run_leases.acquire("insights", "p", steal=True) is True
        assert await _owner_of(leases, "p") == POD_B

    asyncio.run(go())


@pytest.mark.usefixtures("leases")
def test_only_the_owner_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def go() -> None:
        assert await run_leases.acquire("task", "p:1") is True
        _as(POD_B, monkeypatch)
        await run_leases.release("task", "p:1")
        assert await run_leases.is_active("task", "p:1") is True
        _as(POD_A, monkeypatch)
        await run_leases.release("task", "p:1")
        assert await run_leases.is_active("task", "p:1") is False

    asyncio.run(go())


@pytest.mark.usefixtures("leases")
def test_a_stop_request_reaches_the_owner_then_the_lease_is_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = {"p:1"}
    stopped: list[str] = []

    async def stop() -> None:
        stopped.append("p:1")
        running.discard("p:1")

    async def go() -> None:
        assert await run_leases.acquire("task", "p:1") is True
        run_leases.watch("task", "p:1", alive=lambda: "p:1" in running, stop=stop)
        _as(POD_B, monkeypatch)
        assert await run_leases.request_stop("task", "p:1") is True
        # The owner polls on its heartbeat, then releases once the run is gone.
        assert await run_leases.stop_and_wait("task", "p:1", wait_seconds=5) is True

    asyncio.run(go())
    assert stopped == ["p:1"]


def test_a_stolen_lease_stops_the_old_owner(
    leases: async_sessionmaker,  # type: ignore[type-arg]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = {"p"}
    stopped: list[str] = []

    async def stop() -> None:
        stopped.append("p")
        running.discard("p")

    async def go() -> None:
        assert await run_leases.acquire("insights", "p") is True
        run_leases.watch("insights", "p", alive=lambda: "p" in running, stop=stop)
        _as(POD_B, monkeypatch)
        assert await run_leases.acquire("insights", "p", steal=True) is True
        for _ in range(30):
            if stopped:
                break
            await asyncio.sleep(0.1)
        # The old owner never deletes the new owner's lease.
        await asyncio.sleep(0.5)
        assert await _owner_of(leases, "p") == POD_B

    asyncio.run(go())
    assert stopped == ["p"]


@pytest.mark.usefixtures("leases")
def test_a_finished_run_releases_its_lease() -> None:
    running = {"p:1"}

    async def stop() -> None:
        raise AssertionError("a finished run must not be stopped")

    async def go() -> None:
        assert await run_leases.acquire("task", "p:1") is True
        run_leases.watch("task", "p:1", alive=lambda: "p:1" in running, stop=stop)
        running.discard("p:1")
        for _ in range(30):
            if not await run_leases.is_active("task", "p:1"):
                return
            await asyncio.sleep(0.1)
        raise AssertionError("the lease was not released after the run ended")

    asyncio.run(go())


@pytest.mark.usefixtures("leases")
def test_a_database_error_on_heartbeat_does_not_stop_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = {"p:1"}
    stopped: list[str] = []

    async def stop() -> None:
        stopped.append("p:1")

    async def broken_renew(*_args: str) -> bool | None:
        raise RuntimeError("database unreachable")

    async def go() -> None:
        assert await run_leases.acquire("task", "p:1") is True
        monkeypatch.setattr(run_leases, "_renew", broken_renew)
        run_leases.watch("task", "p:1", alive=lambda: "p:1" in running, stop=stop)
        await asyncio.sleep(1.2)  # several heartbeats
        running.discard("p:1")
        await asyncio.sleep(1.2)

    asyncio.run(go())
    assert stopped == []


@pytest.mark.usefixtures("leases")
def test_one_watcher_per_key() -> None:
    running = {"p:1"}

    async def stop() -> None:
        running.discard("p:1")

    async def go() -> None:
        assert await run_leases.acquire("task", "p:1") is True
        run_leases.watch("task", "p:1", alive=lambda: "p:1" in running, stop=stop)
        run_leases.watch("task", "p:1", alive=lambda: "p:1" in running, stop=stop)
        live = [t for t in run_leases._WATCHERS.values() if not t.done()]
        assert len(live) == 1
        running.discard("p:1")
        await asyncio.sleep(1.2)

    asyncio.run(go())
