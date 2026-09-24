"""Shared plan-session store (#755).

`PlanService` used to load every session into a per-process dict at startup and
serve reads from it. Under more than one replica that splits the world: a
discard lands on one pod, persists, and the other pods keep answering with
their startup copy until they restart (measured in prod: four pods, one
`discarded`, three `ingested`). The JSON files on the PVC cannot fix it — the
volume is ReadWriteOnce/local-path, so replicas only share them while they
happen to be co-scheduled on one node.

This store is the copy every replica reads through. It mirrors
:class:`~server.jobstore.store.JobStateStore` deliberately: the same dedicated
background loop, so PFactory's synchronous pipeline can call it from any
thread without the pooled async engine's connections crossing loops.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import Future
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from .plan_session_models import (
    PLAN_SESSION_SCHEMA_VERSION,
    PlanSessionCounter,
    PlanSessionRow,
)

logger = logging.getLogger(__name__)


class PlanSessionStore:
    """Durable, cross-replica plan sessions. Sync-facing, like JobStateStore."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._url = database_url or os.environ.get("DATABASE_URL", "").strip()

        # Dedicated background event loop in its own thread — see JobStateStore:
        # asyncpg binds pooled connections to the creating loop, so all DB I/O
        # for this store runs on exactly one loop whatever thread calls in.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            name="plan-session-store-loop",
            daemon=True,
        )
        self._loop_thread.start()

        if engine is not None:
            self._engine: AsyncEngine = engine
        else:
            if not self._url:
                raise RuntimeError("PlanSessionStore requires DATABASE_URL (or an explicit engine)")
            self._engine = self._run(self._make_engine_coro(self._url))
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    @staticmethod
    async def _make_engine_coro(url: str) -> AsyncEngine:
        return create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)

    def _run(self, coro: Any) -> Any:
        """Run a coroutine on the store's loop and block. Safe from any thread."""
        fut: Future[Any] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    @property
    def supports_row_locking(self) -> bool:
        """True on Postgres (real ``FOR UPDATE``); False on SQLite.

        SQLite serialises writers with a database-level lock, so ``next_seq``
        is still atomic within its transaction there; the row lock only matters
        for the multi-replica Postgres case.
        """
        return self._engine.dialect.name == "postgresql"

    def is_ready(self) -> bool:
        """True when the DB answers AND ``plan_sessions`` exists (migrated).

        Lets the caller fall back to the in-memory path rather than failing,
        e.g. a process that sets DATABASE_URL but never ran migrations. Never
        raises.
        """
        try:
            self._run(self._is_ready_coro())
            return True
        except Exception:  # noqa: BLE001 — any failure == not ready
            return False

    async def _is_ready_coro(self) -> None:
        async with self._sessionmaker() as session:
            await session.execute(select(func.count()).select_from(PlanSessionRow))

    # ── writes ──────────────────────────────────────────────────────────

    def upsert(
        self,
        session_id: str,
        *,
        payload: str,
        seq: int,
        tenant_id: str | None,
        expected_version: int,
    ) -> int | None:
        """Compare-and-set one session's payload (#758).

        ``expected_version == 0`` inserts a new row (version 1); anything else
        updates only if the stored version still matches. Returns the new
        version, or None when the row already exists (insert) or has moved on
        or vanished (update) — the caller holds a stale copy.
        """
        new: int | None = self._run(
            self._upsert_coro(session_id, payload, seq, tenant_id or "default", expected_version)
        )
        return new

    async def _upsert_coro(
        self, session_id: str, payload: str, seq: int, tenant: str, expected: int
    ) -> int | None:
        async with self._sessionmaker() as session, session.begin():
            if expected == 0:
                stmt: Any = (
                    self._insert(PlanSessionRow)
                    .values(
                        session_id=session_id,
                        tenant_id=tenant,
                        seq=seq,
                        schema_version=PLAN_SESSION_SCHEMA_VERSION,
                        payload=payload,
                        version=1,
                    )
                    .on_conflict_do_nothing(index_elements=[PlanSessionRow.session_id])
                    .returning(PlanSessionRow.version)
                )
            else:
                stmt = (
                    update(PlanSessionRow)
                    .where(
                        PlanSessionRow.session_id == session_id,
                        PlanSessionRow.version == expected,
                    )
                    .values(
                        payload=payload,
                        seq=seq,
                        tenant_id=tenant,
                        version=PlanSessionRow.version + 1,
                        updated_at=func.now(),
                    )
                    .returning(PlanSessionRow.version)
                )
            got = (await session.execute(stmt)).scalar_one_or_none()
            return None if got is None else int(got)

    def _insert(self, table: Any) -> Any:
        """The dialect's INSERT, which carries ``on_conflict_do_nothing``."""
        from sqlalchemy.dialects import postgresql, sqlite  # noqa: PLC0415

        if self._engine.dialect.name == "postgresql":
            return postgresql.insert(table)
        return sqlite.insert(table)

    # ── emit lease (#758) ───────────────────────────────────────────────

    def acquire_emit_lease(self, session_id: str, owner: str, ttl_seconds: int) -> bool:
        """Take the session's emit lease if it is free or expired.

        One atomic UPDATE against the database clock, so replicas with skewed
        clocks still agree. False when another owner holds it or the row is
        missing.
        """
        return bool(self._run(self._acquire_coro(session_id, owner, ttl_seconds)))

    async def _acquire_coro(self, session_id: str, owner: str, ttl: int) -> bool:
        now, until = self._now_and_until(ttl)
        stmt = (
            update(PlanSessionRow)
            .where(
                PlanSessionRow.session_id == session_id,
                (PlanSessionRow.emit_lease_until.is_(None))
                | (PlanSessionRow.emit_lease_until < now),
            )
            .values(emit_lease_owner=owner, emit_lease_until=until)
            .returning(PlanSessionRow.session_id)
        )
        async with self._sessionmaker() as session, session.begin():
            return (await session.execute(stmt)).scalar_one_or_none() is not None

    def _now_and_until(self, ttl: int) -> tuple[Any, Any]:
        """The DB clock now, and now + ``ttl`` seconds, as SQL expressions."""
        if self._engine.dialect.name == "postgresql":
            return func.now(), func.now() + func.make_interval(0, 0, 0, 0, 0, 0, ttl)
        # SQLite (tests/dev): datetime() strings compare lexically in order.
        return func.datetime("now"), func.datetime("now", "+" + str(int(ttl)) + " seconds")

    def release_emit_lease(self, session_id: str, owner: str) -> None:
        """Clear the lease, but only if ``owner`` still holds it."""
        self._run(self._release_coro(session_id, owner))

    async def _release_coro(self, session_id: str, owner: str) -> None:
        stmt = (
            update(PlanSessionRow)
            .where(
                PlanSessionRow.session_id == session_id,
                PlanSessionRow.emit_lease_owner == owner,
            )
            .values(emit_lease_owner=None, emit_lease_until=None)
        )
        async with self._sessionmaker() as session, session.begin():
            await session.execute(stmt)

    def next_seq(self) -> int:
        """Allocate the next session number, atomically across replicas.

        An ``UPDATE ... RETURNING`` on one counter row, NOT ``max(seq)+1``:
        allocation and the session insert happen in separate transactions, so
        two replicas reading the maximum could both see the same value and mint
        the same ``NNN-slug`` id — the collision #755 reported. (Postgres also
        rejects ``FOR UPDATE`` with an aggregate, so locking that read is not
        even possible.)
        """
        return int(self._run(self._next_seq_coro()))

    async def _next_seq_coro(self) -> int:
        async with self._sessionmaker() as session, session.begin():
            if self._engine.dialect.name == "postgresql":
                # One statement: insert-or-bump. A read-then-insert raced here
                # (every concurrent caller tried to create id=1 and all but one
                # hit a unique violation), and the counter must work on a store
                # whose seed row is missing as well as on a migrated one.
                from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

                seed = await self._highest_existing_seq(session)
                stmt = (
                    pg_insert(PlanSessionCounter)
                    .values(id=1, value=seed + 1)
                    .on_conflict_do_update(
                        index_elements=[PlanSessionCounter.id],
                        # GREATEST, not value+1: the migration seeds this row at
                        # 0, so on a deployment that then imports existing JSON
                        # sessions the counter would hand out 1 again and collide
                        # with 001-... . The conflict path must clear whatever is
                        # already stored (review on #760).
                        set_={
                            "value": func.greatest(PlanSessionCounter.value, seed) + 1,
                        },
                    )
                    .returning(PlanSessionCounter.value)
                )
                return int((await session.execute(stmt)).scalar_one())

            # SQLite (tests/dev): one writer at a time, so read-modify-write
            # inside the transaction is already serialised.
            row = await session.get(PlanSessionCounter, 1, with_for_update=False)
            highest = await self._highest_existing_seq(session)
            if row is None:
                row = PlanSessionCounter(id=1, value=highest)
                session.add(row)
                await session.flush()
            # max(): same reason as the Postgres branch — a seeded-at-zero
            # counter must not reissue an imported session's number.
            row.value = max(int(row.value), highest) + 1
            return int(row.value)

    async def _highest_existing_seq(self, session: Any) -> int:
        """The largest session number already stored.

        Seeds the counter on a store that has sessions but no counter row (an
        upgraded deployment), so allocation never reissues an existing id.
        """
        stmt = select(func.coalesce(func.max(PlanSessionRow.seq), 0))
        return int((await session.execute(stmt)).scalar_one())

    # ── reads ───────────────────────────────────────────────────────────

    def get(self, session_id: str) -> tuple[str, int] | None:
        """``(payload, version)`` for ``session_id``, or None when absent."""
        # `_run` is typed Any (it marshals any coroutine); bind it to the real
        # type here rather than returning Any from a typed signature.
        found: tuple[str, int] | None = self._run(self._get_coro(session_id))
        return found

    async def _get_coro(self, session_id: str) -> tuple[str, int] | None:
        async with self._sessionmaker() as session:
            row = await session.get(PlanSessionRow, session_id)
            return None if row is None else (row.payload, int(row.version))

    def list_payloads(self, *, tenant_id: str | None = None) -> list[str]:
        """Every stored payload, oldest session number first."""
        return list(self._run(self._list_coro(tenant_id)))

    async def _list_coro(self, tenant_id: str | None) -> list[str]:
        async with self._sessionmaker() as session:
            stmt = select(PlanSessionRow).order_by(PlanSessionRow.seq)
            if tenant_id is not None:
                stmt = stmt.where(PlanSessionRow.tenant_id == tenant_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [r.payload for r in rows]

    def close(self) -> None:
        """Dispose the engine and stop the background loop.

        Every instance owns a loop, a thread and a pool, so a caller that
        decides not to use one (an unmigrated DB) MUST close it — otherwise a
        process that builds many PlanServices leaks all three each time.
        """
        try:
            self._run(self._engine.dispose())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5)

    def session_ids(self) -> set[str]:
        """Ids already stored — used by the one-shot JSON import (#755)."""
        return set(self._run(self._ids_coro()))

    async def _ids_coro(self) -> set[str]:
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(PlanSessionRow.session_id))).scalars().all()
            return set(rows)
