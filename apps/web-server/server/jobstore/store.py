"""Postgres-backed job-state store with multi-replica-safe admission.

RFC-0016 #217. Replaces PFactory's per-pod in-memory ``_sessions`` dict and
the optional per-pod JSON mirror with a row per job in shared Postgres, so
state survives a restart and is consistent across replicas.

Threading / async model
-----------------------
PFactory's ``process()`` runs in a worker thread (``asyncio.to_thread``,
#219), and the store's public methods are synchronous. Rather than spin up a
fresh event loop + connection per call (``asyncio.run`` + ``NullPool``, which
pays a full TCP+auth handshake on every ``_save`` and made the
whole-suite-against-Postgres lane time out), the store owns **one dedicated
background event loop in its own thread** with a normal **pooled** async
engine bound to that loop. Sync calls marshal their coroutine onto that loop
via :func:`asyncio.run_coroutine_threadsafe` and block for the result. This
keeps a persistent connection pool (fast, no per-call reconnect) while still
being safe from any thread, and keeps the store independent of the app's
request-loop engine (``server.database.engine``).

Concurrency rule (conventions §1)
---------------------------------
:meth:`try_start` grants a concurrency slot inside a single transaction. To be
both deadlock-free AND cap-correct under real multi-replica concurrency it
serialises the count-then-flip critical section with a transaction-scoped
``pg_advisory_xact_lock`` (a single, totally-ordered gate) rather than a
``FOR UPDATE`` over the whole in-flight set (which deadlocks when two
transactions lock different rows and wait on each other). Two replicas racing
for the last slot serialise cleanly on the advisory lock, so the cap can never
be exceeded and a ``job_id`` cannot be double-started.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import Future
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .lifecycle import (
    IN_FLIGHT_LIFECYCLE,
    TERMINAL_LIFECYCLE,
    lifecycle_state_for,
)
from .models import JOB_STATE_SCHEMA_VERSION, JobState

logger = logging.getLogger(__name__)

# Fixed key for the admission critical-section advisory lock (Postgres). Any
# stable 64-bit int works; this one is arbitrary but constant so every replica
# (and every grant) contends on the SAME lock. Scoped to pfactory's plan
# admission only.
_ADMISSION_LOCK_KEY = 0x5046414354 & 0x7FFFFFFFFFFFFFFF  # "PFACT" as int, masked


class SlotDenied(RuntimeError):
    """Raised by :meth:`JobStateStore.try_start` when the cap is reached.

    The caller decides whether to wait-and-retry (queue) or surface the
    back-pressure. PFactory's admission gate retries with backoff so callers
    queue rather than error, preserving the in-memory semaphore's behaviour.
    """


def jobstore_enabled() -> bool:
    """True when a real ``DATABASE_URL`` is configured (Postgres or sqlite).

    Whitespace-only is treated as unset. When false, PFactory keeps the
    in-memory store and logs that it is not multi-replica safe.
    """
    return bool(os.environ.get("DATABASE_URL", "").strip())


def lifecycle_filter() -> tuple[str, ...]:
    """The lifecycle states a fresh replica reconstructs in-flight from."""
    return IN_FLIGHT_LIFECYCLE


def _now() -> datetime:
    return datetime.now(UTC)


class JobStateStore:
    """Durable job-state store for PFactory plan jobs (one row per job).

    Pass an explicit ``database_url`` / ``engine`` in tests; in production the
    URL is resolved from the ``DATABASE_URL`` env var. ``max_concurrent`` is
    the admission cap (``<= 0`` means unlimited).
    """

    def __init__(
        self,
        *,
        database_url: str | None = None,
        engine: AsyncEngine | None = None,
        max_concurrent: int | None = None,
    ) -> None:
        self._url = database_url or os.environ.get("DATABASE_URL", "").strip()
        # An explicit cap (store unit tests) is fixed; otherwise the cap is read
        # LIVE from PFACTORY_MAX_CONCURRENT_PLANS on every grant. The store is a
        # process-wide singleton, so a fixed-at-construction cap would ignore a
        # later env change (and break callers/tests that vary the cap).
        self._fixed_cap = max_concurrent

        # Dedicated background event loop in its own thread. All DB I/O runs on
        # this single loop, so the pooled async engine's connections are never
        # used cross-loop. Sync callers (from any thread) marshal coroutines
        # onto it via run_coroutine_threadsafe.
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever,
            name="jobstore-loop",
            daemon=True,
        )
        self._loop_thread.start()

        if engine is not None:
            self._engine: AsyncEngine = engine
        else:
            if not self._url:
                raise RuntimeError("JobStateStore requires DATABASE_URL (or an explicit engine)")
            # Pooled engine created ON the background loop (asyncpg binds its
            # connections to the creating loop). pool_pre_ping guards against a
            # stale connection after an idle period.
            self._engine = self._run(self._make_engine_coro(self._url))
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

    @staticmethod
    async def _make_engine_coro(url: str) -> AsyncEngine:
        return create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)

    def _cap(self) -> int:
        """The admission cap: the explicit fixed cap, else the live env value."""
        return self._fixed_cap if self._fixed_cap is not None else _resolve_cap()

    def _run(self, coro: Any) -> Any:
        """Run a coroutine on the store's dedicated background loop and block.

        Safe to call from any thread (PFactory's worker threads, the event-loop
        thread, or tests): the coroutine always executes on ``self._loop``, so
        the pooled engine's connections stay on a single loop. We submit via
        :func:`asyncio.run_coroutine_threadsafe` and wait for the result.
        """
        fut: Future[Any] = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    def is_ready(self) -> bool:
        """True when the backing DB is reachable AND the ``job_states`` table
        exists (migrations applied).

        Used so callers can fall back to the in-memory path when the durable
        store can't actually serve requests yet — e.g. a unit-test process that
        sets ``DATABASE_URL`` but never ran migrations, or a control-plane that
        booted before the Alembic Job finished. Never raises.
        """
        try:
            self._run(self._is_ready_coro())
            return True
        except Exception:  # noqa: BLE001 — any failure == not ready
            return False

    async def _is_ready_coro(self) -> None:
        async with self._sessionmaker() as session:
            await session.execute(select(func.count()).select_from(JobState))

    @property
    def supports_row_locking(self) -> bool:
        """True on Postgres (real ``FOR UPDATE``); False on SQLite.

        SQLite has no row-level ``FOR UPDATE`` — its writer is already
        serialised by a database-level lock, so the count-then-flip in
        :meth:`try_start` is still atomic within one transaction, but the
        multi-replica race the convention targets only exists on Postgres.
        Tests assert the cap holds on both; the FOR-UPDATE clause is emitted
        only where the dialect supports it.
        """
        return self._engine.dialect.name == "postgresql"

    # ── lifecycle writes ────────────────────────────────────────────────

    def upsert(
        self,
        job_id: str,
        *,
        service_status: str,
        correlation_key: str | int | None = None,
        phase: str | None = None,
        result: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        admission: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update a job's row, mapping native status -> lifecycle.

        Sets ``ended_at`` automatically when the new state is terminal, and
        records ``result``/``error`` (a terminal failure MUST carry a reason —
        when ``error`` is omitted for a failed/stuck state a generic reason is
        stored so the never-overclaim rule cannot be violated). Returns the
        row as a dict.
        """
        return self._run(
            self._upsert_async(
                job_id,
                service_status=service_status,
                correlation_key=correlation_key,
                phase=phase,
                result=result,
                usage=usage,
                error=error,
                admission=admission,
            )
        )

    async def _upsert_async(
        self,
        job_id: str,
        *,
        service_status: str,
        correlation_key: str | int | None,
        phase: str | None,
        result: dict[str, Any] | None,
        usage: dict[str, Any] | None,
        error: str | None,
        admission: dict[str, Any] | None,
    ) -> dict[str, Any]:
        lifecycle = lifecycle_state_for(service_status)
        terminal = lifecycle in TERMINAL_LIFECYCLE
        async with self._sessionmaker() as session:
            async with session.begin():
                row = await session.get(JobState, job_id)
                if row is None:
                    row = JobState(
                        job_id=job_id,
                        schema_version=JOB_STATE_SCHEMA_VERSION,
                        service="pfactory",
                        kind="plan",
                        attempt=1,
                    )
                    session.add(row)
                row.service_status = service_status
                row.lifecycle_state = lifecycle
                if correlation_key is not None:
                    row.correlation_key = str(correlation_key)
                if phase is not None:
                    row.phase = phase
                if result is not None:
                    row.result = result
                if usage is not None:
                    row.usage = usage
                if admission is not None:
                    row.admission = {**(row.admission or {}), **admission}
                if terminal:
                    row.ended_at = _now()
                    if lifecycle == "failed":
                        # never-overclaim: a terminal failure MUST carry a reason.
                        row.error = error or "plan failed (no detail provided)"
                    elif error is not None:
                        row.error = error
                elif error is not None:
                    row.error = error
                await session.flush()
                # Refresh so server-side defaults (created_at/updated_at) are
                # loaded before we serialise — otherwise _to_dict triggers a
                # lazy refresh outside the async greenlet context.
                await session.refresh(row)
                return _to_dict(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        """Return a job's row as a dict, or ``None`` if absent."""
        return self._run(self._get_async(job_id))

    async def _get_async(self, job_id: str) -> dict[str, Any] | None:
        async with self._sessionmaker() as session:
            row = await session.get(JobState, job_id)
            return _to_dict(row) if row is not None else None

    def in_flight_count(self) -> int:
        """Live count of jobs in (queued, running) — the admission basis."""
        return self._run(self._in_flight_count_async())

    async def _in_flight_count_async(self) -> int:
        async with self._sessionmaker() as session:
            stmt = select(func.count()).where(JobState.lifecycle_state.in_(IN_FLIGHT_LIFECYCLE))
            return int((await session.execute(stmt)).scalar_one())

    def running_count(self) -> int:
        """Live count of jobs in ``running`` — what the cap limits."""
        return self._run(self._running_count_async())

    async def _running_count_async(self) -> int:
        async with self._sessionmaker() as session:
            stmt = select(func.count()).where(JobState.lifecycle_state == "running")
            return int((await session.execute(stmt)).scalar_one())

    # ── admission: grant a concurrency slot under a row lock ─────────────

    def try_start(self, job_id: str) -> dict[str, Any]:
        """Atomically grant a concurrency slot for ``job_id`` -> ``running``.

        In ONE transaction, serialised against every other replica granting a
        slot: take the admission critical-section lock (a transaction-scoped
        ``pg_advisory_xact_lock`` on Postgres — see :meth:`_acquire_admission_lock`),
        count how many jobs are already ``running``, and only if that is below
        the cap flip this job's row to ``running`` + stamp
        ``admission.started_at``. The advisory lock (not per-row ``FOR UPDATE``
        on the whole in-flight set) is what makes this both **deadlock-free**
        and **cap-correct** under real concurrency: every grant takes the SAME
        single lock in the SAME order, so two replicas racing for the last slot
        serialise cleanly instead of each grabbing a different row and waiting
        on the other (the classic FOR-UPDATE-over-a-set deadlock).

        Raises :class:`SlotDenied` when the cap is full (the caller queues/
        retries) and is idempotent for a row already ``running`` (returns it
        without consuming a second slot). An unlimited cap (``<= 0``) skips the
        count and starts immediately.
        """
        return self._run(self._try_start_async(job_id))

    async def _acquire_admission_lock(self, session: AsyncSession) -> None:
        """Serialise the admission critical section across replicas.

        Postgres: a transaction-scoped advisory lock on a fixed key — released
        automatically at COMMIT/ROLLBACK, and every grant uses the same key, so
        there is a single, totally-ordered gate (no row-ordering deadlock).
        SQLite: a single-process writer is already serialised by the database
        lock, so no extra lock is needed here.
        """
        if self._engine.dialect.name == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:k)").bindparams(
                    bindparam("k", _ADMISSION_LOCK_KEY)
                )
            )

    async def _try_start_async(self, job_id: str) -> dict[str, Any]:
        async with self._sessionmaker() as session:
            async with session.begin():
                # Enter the admission critical section (deadlock-free gate).
                await self._acquire_admission_lock(session)

                # Lock just THIS job's row so two grants of the same job_id
                # serialise (idempotency) — a single row, so no ordering issue.
                me = await session.get(JobState, job_id, with_for_update=self.supports_row_locking)
                if me is None:
                    raise SlotDenied(f"job '{job_id}' has no queued row to start")

                # Idempotent: already running -> return without a second slot.
                if me.lifecycle_state == "running":
                    return _to_dict(me)

                # Count running jobs under the advisory lock — a plain COUNT is
                # accurate here because no other grant can run concurrently.
                cap = self._cap()
                if cap > 0:
                    count_stmt = select(func.count()).where(JobState.lifecycle_state == "running")
                    running = int((await session.execute(count_stmt)).scalar_one())
                    if running >= cap:
                        raise SlotDenied(f"admission cap {cap} reached ({running} running)")

                me.lifecycle_state = "running"
                me.service_status = "processing"
                me.admission = {
                    **(me.admission or {}),
                    "started_at": _now().isoformat(),
                }
                await session.flush()
                # Refresh so the onupdate-bumped updated_at is loaded before
                # serialising (avoids a lazy refresh outside the greenlet).
                await session.refresh(me)
                return _to_dict(me)

    def close(self) -> None:
        """Dispose the engine and stop the background loop (tests / shutdown)."""
        try:
            self._run(self._engine.dispose())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5)


def _resolve_cap() -> int:
    """Admission cap from ``PFACTORY_MAX_CONCURRENT_PLANS`` (default 4)."""
    try:
        return int(os.environ.get("PFACTORY_MAX_CONCURRENT_PLANS", "4"))
    except ValueError:
        return 4


def _to_dict(row: JobState) -> dict[str, Any]:
    """Serialise a :class:`JobState` row to a plain dict (schema-shaped)."""
    return {
        "schema_version": row.schema_version,
        "job_id": row.job_id,
        "correlation_key": row.correlation_key,
        "service": row.service,
        "kind": row.kind,
        "lifecycle_state": row.lifecycle_state,
        "service_status": row.service_status,
        "phase": row.phase,
        "attempt": row.attempt,
        "admission": row.admission,
        "worker_ref": row.worker_ref,
        "artifacts": row.artifacts,
        "result": row.result,
        "usage": row.usage,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
    }
